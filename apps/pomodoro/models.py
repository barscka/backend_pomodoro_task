from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver


DEFAULT_CATEGORY_ID = 1
DEFAULT_CATEGORY_NAME = 'Todos'
DEFAULT_CATEGORY_DESCRIPTION = 'Categoria padrao para atividades sem classificacao especifica.'
DEFAULT_CATEGORY_COLOR = '#FFFFFF'
DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS = 2


def get_default_group_id():
    group, _ = Group.objects.get_or_create(
        is_default=True,
        defaults={
            'name': 'Todos',
            'description': 'Grupo padrao que mantem o comportamento atual.',
            'color': '#FFFFFF',
        },
    )
    return group.id


class Group(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#FFFFFF')
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Group'
        verbose_name_plural = 'Groups'
        ordering = ['name']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Group.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#FFFFFF')
    max_daily_executions = models.PositiveIntegerField(default=2)  # Novo campo para o limite
    executions_today = models.PositiveIntegerField(default=0)
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name='categories',
        default=get_default_group_id,
    )
    
    @property
    def current_executions(self):
        """Retorna o total de execuções hoje para todas as atividades desta categoria"""
        today = timezone.now().date()
        return History.objects.filter(
            activity__category=self,
            start_time__date=today
        ).count()
    
    def can_execute_more(self):
        """Verifica se ainda pode executar atividades desta categoria hoje"""
        return self.current_executions < self.max_daily_executions
    
    def clean(self):
        if not self.pk:
            return
        """Validação para o limite de execuções"""
        if self.current_executions >= self.max_daily_executions:
            raise ValidationError(
                f"Limite diário de {self.max_daily_executions} execuções atingido para esta categoria"
            )
    
    class Meta:
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


def get_default_category_id():
    default_group = Group.objects.filter(is_default=True).first()
    if not default_group:
        default_group_id = get_default_group_id()
        default_group = Group.objects.get(pk=default_group_id)

    category, _ = Category.objects.get_or_create(
        pk=DEFAULT_CATEGORY_ID,
        defaults={
            'name': DEFAULT_CATEGORY_NAME,
            'description': DEFAULT_CATEGORY_DESCRIPTION,
            'color': DEFAULT_CATEGORY_COLOR,
            'max_daily_executions': DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS,
            'executions_today': 0,
            'group': default_group,
        },
    )
    return category.id


class Activity(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    duration = models.IntegerField(default=60)  # em minutos
    active = models.BooleanField(default=True)
    premium = models.BooleanField(default=False)
    premium_from = models.DateField(null=True, blank=True)
    premium_until = models.DateField(null=True, blank=True)
    category = models.ForeignKey(
        Category, 
        on_delete=models.PROTECT,
        default=get_default_category_id,
        related_name='activities'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_executed = models.DateTimeField(null=True, blank=True)
    executions_today = models.IntegerField(default=0)
    priority = models.IntegerField(default=1) 

    class Meta:
        verbose_name = 'Activity'
        verbose_name_plural = 'Activities'
        ordering = ['-premium', 'name']

    @property
    def is_premium_active(self):
        if not self.premium:
            return False

        today = timezone.localdate()
        if self.premium_from and self.premium_from > today:
            return False
        if self.premium_until and self.premium_until < today:
            return False
        return True

    def can_execute(self, selected_group=None):
        if not self.active:
            return False

        if not self.category:
            return False

        if selected_group and not selected_group.is_default:
            return self.category.group_id == selected_group.id and self.category.can_execute_more()

        return self.category.can_execute_more()

    def remaining_executions(self, selected_group=None):
        if not self.category:
            return None

        if selected_group and not selected_group.is_default:
            if self.category.group_id != selected_group.id:
                return 0
            return max(self.category.max_daily_executions - self.category.current_executions, 0)

        return max(self.category.max_daily_executions - self.category.current_executions, 0)
    
    def clean(self):
        """Validação temporária usando executions_today"""
        if self.premium:
            if not self.premium_from or not self.premium_until:
                raise ValidationError(
                    "Atividades premium precisam informar premium_from e premium_until."
                )
            if self.premium_from > self.premium_until:
                raise ValidationError(
                    "premium_from não pode ser maior que premium_until."
                )

        if self.category and self.executions_today >= self.category.max_daily_executions:
            raise ValidationError(
                f"Limite diário de {self.category.max_daily_executions} execuções atingido para esta categoria"
            )

    def __str__(self):
        return self.name


class Schedule(models.Model):
    STATE_PREPARING = 'preparing'
    STATE_RUNNING = 'running'
    STATE_COMPLETED = 'completed'
    STATE_CANCELLED = 'cancelled'
    STATE_EXPIRED = 'expired'
    STATE_CHOICES = [
        (STATE_PREPARING, 'Preparing'),
        (STATE_RUNNING, 'Running'),
        (STATE_COMPLETED, 'Completed'),
        (STATE_CANCELLED, 'Cancelled'),
        (STATE_EXPIRED, 'Expired'),
    ]

    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name='schedules'
    )
    scheduled_date = models.DateField()
    start_time = models.TimeField() 
    end_time = models.TimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    queue_item = models.OneToOneField(
        'ActivityQueueItem',
        on_delete=models.PROTECT,
        related_name='schedule',
        null=True,
        blank=True,
    )
    scope_key = models.CharField(max_length=64, blank=True, default='', db_index=True)
    state = models.CharField(
        max_length=16,
        choices=STATE_CHOICES,
        default=STATE_RUNNING,
    )
    version = models.PositiveIntegerField(default=1)
    requested_at = models.DateTimeField(null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    expected_end_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('activity', 'scheduled_date')
        ordering = ['scheduled_date']
        constraints = [
            models.UniqueConstraint(
                fields=['scope_key'],
                condition=Q(state__in=['preparing', 'running']) & ~Q(scope_key=''),
                name='unique_open_schedule_per_scope',
            ),
        ]

    def __str__(self):
        return f"Schedule {self.id} for {self.scheduled_date}"


class ActivityQueue(models.Model):
    STATE_ACTIVE = 'active'
    STATE_CLOSED = 'closed'
    STATE_CANCELLED = 'cancelled'
    STATE_CHOICES = [
        (STATE_ACTIVE, 'Active'),
        (STATE_CLOSED, 'Closed'),
        (STATE_CANCELLED, 'Cancelled'),
    ]

    MODE_NORMAL = 'normal'
    MODE_SKIPPED_REVIEW = 'skipped_review'
    MODE_CHOICES = [
        (MODE_NORMAL, 'Normal'),
        (MODE_SKIPPED_REVIEW, 'Skipped review'),
    ]

    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name='activity_queues',
        null=True,
        blank=True,
    )
    scope_key = models.CharField(max_length=64, db_index=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    mode = models.CharField(max_length=24, choices=MODE_CHOICES, default=MODE_NORMAL)
    pool_number = models.PositiveIntegerField(default=1)
    pool_size = models.PositiveIntegerField(default=0)
    consumed_count = models.PositiveIntegerField(default=0)
    skip_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['scope_key'],
                condition=Q(state='active'),
                name='unique_active_queue_per_scope',
            ),
        ]

    def __str__(self):
        return f"Queue {self.id} ({self.scope_key})"


class ActivityQueueItem(models.Model):
    STATE_PENDING = 'pending'
    STATE_PRESENTED = 'presented'
    STATE_STARTED = 'started'
    STATE_COMPLETED = 'completed'
    STATE_SKIPPED = 'skipped'
    STATE_EXPIRED = 'expired'
    STATE_CHOICES = [
        (STATE_PENDING, 'Pending'),
        (STATE_PRESENTED, 'Presented'),
        (STATE_STARTED, 'Started'),
        (STATE_COMPLETED, 'Completed'),
        (STATE_SKIPPED, 'Skipped'),
        (STATE_EXPIRED, 'Expired'),
    ]

    queue = models.ForeignKey(
        ActivityQueue,
        on_delete=models.CASCADE,
        related_name='items',
    )
    activity = models.ForeignKey(
        Activity,
        on_delete=models.PROTECT,
        related_name='queue_items',
    )
    position = models.PositiveIntegerField()
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_PENDING)
    presented_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['queue', 'position'],
                name='unique_queue_item_position',
            ),
        ]

    def __str__(self):
        return f"QueueItem {self.id} ({self.activity_id})"


class ActivityPreferenceEvent(models.Model):
    EVENT_FAVORITE_COMPLETED = 'favorite_completed'
    EVENT_SKIPPED = 'skipped'
    EVENT_SKIPPED_COMPLETED = 'skipped_completed'
    EVENT_CHOICES = [
        (EVENT_FAVORITE_COMPLETED, 'Favorite completed'),
        (EVENT_SKIPPED, 'Skipped'),
        (EVENT_SKIPPED_COMPLETED, 'Skipped completed'),
    ]

    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name='preference_events',
    )
    queue = models.ForeignKey(
        ActivityQueue,
        on_delete=models.CASCADE,
        related_name='preference_events',
    )
    queue_item = models.ForeignKey(
        ActivityQueueItem,
        on_delete=models.CASCADE,
        related_name='preference_events',
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    weight_delta = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f"{self.event_type} for activity {self.activity_id}"


@receiver(pre_delete, sender=Category)
def prevent_default_category_delete(sender, instance, **kwargs):
    if instance.pk == DEFAULT_CATEGORY_ID and instance.name == DEFAULT_CATEGORY_NAME:
        raise ValidationError('A categoria padrao Todos nao pode ser removida.')

class History(models.Model):
    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name='histories'
    )
    schedule = models.OneToOneField(  # Relação 1:1
        Schedule,
        on_delete=models.CASCADE,
        related_name='execution_history'
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(null=True, blank=True)  # em minutos
    notes = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = 'History'
        verbose_name_plural = 'Histories'
        ordering = ['-start_time']

    def save(self, *args, **kwargs):
        """Atualiza o contador ao criar um novo histórico"""
        if not self.pk:  # Se for uma criação nova
            self.activity.executions_today += 1
            self.activity.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"History {self.id} of {self.activity.name}"
