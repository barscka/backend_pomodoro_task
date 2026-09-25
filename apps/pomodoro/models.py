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
    group_id = Group.objects.filter(is_default=True).values_list('id', flat=True).first()
    if group_id:
        return group_id
    group, _ = Group.objects.get_or_create(
        name='Todos',
        defaults={
            'description': 'Grupo padrao que mantem o comportamento atual.',
            'color': '#FFFFFF',
        },
    )
    group.is_default = True
    group.save(update_fields=['is_default'])
    return group.id


class Group(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#FFFFFF')
    is_default = models.BooleanField(default=False)
    max_daily_minutes = models.PositiveIntegerField(default=0)
    is_retro_catalog = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = 'Group'
        verbose_name_plural = 'Groups'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['is_retro_catalog'],
                condition=Q(is_retro_catalog=True),
                name='unique_retro_catalog_group',
            ),
        ]

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
    retro_sort_order = models.PositiveIntegerField(null=True, blank=True)
    
    @property
    def current_executions(self):
        """Retorna o total de execuções hoje para todas as atividades desta categoria"""
        today = timezone.now().date()
        return History.objects.filter(
            activity__category=self,
            start_time__date=today,
            schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY],
        ).count()
    
    def can_execute_more(self):
        """Verifica se ainda pode executar atividades desta categoria hoje"""
        return self.current_executions < self.max_daily_executions
    
    def clean(self):
        if self.retro_sort_order is not None and self.group_id and not self.group.is_retro_catalog:
            raise ValidationError({'retro_sort_order': 'A ordem retrô só pode ser usada no grupo RetroGames.'})
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
    # Migration callables import the current model even while replaying an older
    # schema. Select only the PK so additive fields introduced later are not read.
    existing_id = Category.objects.only('id').filter(pk=DEFAULT_CATEGORY_ID).values_list('id', flat=True).first()
    if existing_id:
        return existing_id
    default_group_id = Group.objects.filter(is_default=True).values_list('id', flat=True).first()
    if not default_group_id:
        default_group_id = get_default_group_id()

    category, _ = Category.objects.get_or_create(
        pk=DEFAULT_CATEGORY_ID,
        defaults={
            'name': DEFAULT_CATEGORY_NAME,
            'description': DEFAULT_CATEGORY_DESCRIPTION,
            'color': DEFAULT_CATEGORY_COLOR,
            'max_daily_executions': DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS,
            'executions_today': 0,
            'group_id': default_group_id,
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
    # Identidade genérica para importações externas, sem acoplar Activity à Steam.
    external_source = models.CharField(max_length=30, blank=True, default='', db_index=True)
    external_id = models.CharField(max_length=50, blank=True, default='', db_index=True)

    class Meta:
        verbose_name = 'Activity'
        verbose_name_plural = 'Activities'
        ordering = ['-premium', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['external_source', 'external_id'],
                condition=~Q(external_source='') & ~Q(external_id=''),
                name='unique_activity_external_identity',
            ),
        ]

    @property
    def is_premium_active(self):
        if not self.premium or not self.premium_from or not self.premium_until:
            return False

        today = timezone.localdate()
        if self.premium_from > today:
            return False
        if self.premium_until < today:
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
    ORIGIN_QUEUE = 'queue'
    ORIGIN_PREMIUM_DIRECT = 'premium_direct'
    ORIGIN_RETRO_DIRECT = 'retro_direct'
    ORIGIN_LEGACY = 'legacy'
    ORIGIN_CHOICES = [
        (ORIGIN_QUEUE, 'Queue'),
        (ORIGIN_PREMIUM_DIRECT, 'Premium direct'),
        (ORIGIN_RETRO_DIRECT, 'Retro direct'),
        (ORIGIN_LEGACY, 'Legacy'),
    ]
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
    goal_category_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    goal_group_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
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
    execution_origin = models.CharField(max_length=20, choices=ORIGIN_CHOICES, default=ORIGIN_QUEUE, db_index=True)
    premium_period = models.ForeignKey('PremiumPeriod', null=True, blank=True, on_delete=models.PROTECT, related_name='schedules')
    planned_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    continued_from = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='continuation')
    return_group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.PROTECT, related_name='return_schedules')
    retro_game = models.ForeignKey(
        'RetroGame', null=True, blank=True, on_delete=models.PROTECT, related_name='schedules'
    )

    class Meta:
        ordering = ['scheduled_date']
        constraints = [
            models.UniqueConstraint(
                fields=['scope_key'],
                condition=Q(state__in=['preparing', 'running']) & ~Q(scope_key=''),
                name='unique_open_schedule_per_scope',
            ),
            models.CheckConstraint(
                condition=(
                    Q(execution_origin='retro_direct', retro_game__isnull=False,
                      queue_item__isnull=True, premium_period__isnull=True)
                    | (~Q(execution_origin='retro_direct') & Q(retro_game__isnull=True))
                ),
                name='schedule_retro_origin_context',
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
        default=get_default_group_id,
    )
    source_queue = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        related_name='review_queue',
        null=True,
        blank=True,
    )
    recreated_from = models.OneToOneField(
        'self',
        on_delete=models.PROTECT,
        related_name='recreated_queue',
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
                fields=['scope_key', 'group'],
                condition=Q(state='active'),
                name='unique_active_queue_per_scope_group',
            ),
        ]
        indexes = [
            models.Index(fields=['scope_key', 'group', 'state'], name='queue_scope_group_state_idx'),
            models.Index(fields=['source_queue', 'mode'], name='queue_source_mode_idx'),
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
            models.UniqueConstraint(
                fields=['queue', 'activity'],
                name='unique_activity_per_queue',
            ),
        ]
        indexes = [
            models.Index(fields=['queue', 'state', 'position'], name='queue_item_state_pos_idx'),
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
        if not self.pk and self.schedule.execution_origin not in [
            Schedule.ORIGIN_PREMIUM_DIRECT,
            Schedule.ORIGIN_RETRO_DIRECT,
        ]:
            self.activity.executions_today += 1
            self.activity.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"History {self.id} of {self.activity.name}"


class WeeklyGoal(models.Model):
    METRIC_CHOICES = [('minutes', 'Minutos'), ('sessions', 'Sessões')]

    scope_key = models.CharField(max_length=64)
    metric = models.CharField(max_length=8, choices=METRIC_CHOICES)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    is_all_groups = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=(Q(group__isnull=False, category__isnull=True)
                           | Q(group__isnull=True, category__isnull=False)),
                name='goal_exactly_one_destination',
            ),
            models.CheckConstraint(
                condition=Q(is_all_groups=False) | Q(group__isnull=False),
                name='goal_all_requires_group',
            ),
            models.CheckConstraint(condition=Q(metric__in=['minutes', 'sessions']), name='goal_valid_metric'),
            models.CheckConstraint(condition=Q(version__gte=1), name='goal_positive_version'),
            models.UniqueConstraint(fields=['scope_key', 'metric', 'group'],
                                    condition=Q(group__isnull=False), name='goal_unique_group_metric'),
            models.UniqueConstraint(fields=['scope_key', 'metric', 'category'],
                                    condition=Q(category__isnull=False), name='goal_unique_category_metric'),
        ]


class WeeklyGoalRevision(models.Model):
    goal = models.ForeignKey(WeeklyGoal, on_delete=models.CASCADE, related_name='revisions')
    effective_week = models.DateField()
    target = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['effective_week', 'id']
        constraints = [
            models.UniqueConstraint(fields=['goal', 'effective_week'], name='goal_unique_revision_week'),
            models.CheckConstraint(condition=Q(target__gte=1), name='goal_positive_target'),
            models.CheckConstraint(condition=Q(effective_week__week_day=2), name='goal_revision_monday'),
        ]


class GoalCompletion(models.Model):
    # Deliberately not FKs: these facts survive deletion of their source records.
    source_schedule_id = models.PositiveBigIntegerField(unique=True)
    scope_key = models.CharField(max_length=64)
    category_id_snapshot = models.PositiveBigIntegerField()
    group_id_snapshot = models.PositiveBigIntegerField()
    completed_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField()
    activity_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    activity_name_snapshot = models.CharField(max_length=100, blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    execution_origin = models.CharField(max_length=20, choices=Schedule.ORIGIN_CHOICES, default=Schedule.ORIGIN_LEGACY)
    context_source = models.CharField(max_length=16, choices=[
        ('execution_start', 'Início da execução'), ('legacy_current', 'Contexto atual do legado'),
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['scope_key', 'completed_at'], name='goal_fact_scope_time_idx'),
            models.Index(fields=['scope_key', 'group_id_snapshot', 'completed_at'], name='goal_fact_group_time_idx'),
            models.Index(fields=['scope_key', 'category_id_snapshot', 'completed_at'], name='goal_fact_category_time_idx'),
        ]


class PremiumPeriod(models.Model):
    KIND_PAID = 'paid'
    KIND_FOCUS = 'focus'
    KIND_CHOICES = [(KIND_PAID, 'Pago'), (KIND_FOCUS, 'Foco')]

    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name='premium_periods')
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    title = models.CharField(max_length=120)
    starts_on = models.DateField()
    ends_on = models.DateField()
    timezone = models.CharField(max_length=64, default='America/Sao_Paulo')
    ended_early_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=24, default='native')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_on', '-id']
        constraints = [models.CheckConstraint(condition=Q(ends_on__gte=models.F('starts_on')), name='premium_period_valid_dates')]


class ExecutionIdempotency(models.Model):
    scope_key = models.CharField(max_length=64)
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    schedule = models.ForeignKey(Schedule, null=True, blank=True, on_delete=models.SET_NULL, related_name='idempotency_records')
    schedule_id_tombstone = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'request_id'], name='execution_request_scope_unique')]


class GameplayTrackingSettings(models.Model):
    scope_key = models.CharField(max_length=64, unique=True)
    daily_reference_minutes = models.PositiveIntegerField(default=300)
    groups = models.ManyToManyField(Group, blank=True, related_name='gameplay_settings')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RetroPlatform(models.Model):
    generation = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='retro_platforms')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    manufacturer = models.CharField(max_length=100, blank=True, default='')
    release_year = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['generation__retro_sort_order', 'sort_order', 'release_year', 'name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['generation', 'name'], name='retro_platform_generation_name_unique'),
            models.CheckConstraint(condition=Q(sort_order__gte=1), name='retro_platform_positive_order'),
            models.CheckConstraint(
                condition=Q(release_year__isnull=True) | Q(release_year__range=(1970, 2100)),
                name='retro_platform_valid_release_year',
            ),
        ]

    def clean(self):
        if self.generation_id and not self.generation.group.is_retro_catalog:
            raise ValidationError({'generation': 'A geração deve pertencer ao grupo RetroGames.'})

    def __str__(self):
        return self.name


class RetroGame(models.Model):
    TIER_ESSENTIAL = 'essential'
    TIER_COMPLEMENTARY = 'complementary'
    TIER_CHOICES = [(TIER_ESSENTIAL, 'Essencial'), (TIER_COMPLEMENTARY, 'Complementar')]

    activity = models.OneToOneField(Activity, on_delete=models.PROTECT, related_name='retro_game')
    platform = models.ForeignKey(RetroPlatform, on_delete=models.PROTECT, related_name='games')
    tier = models.CharField(max_length=16, choices=TIER_CHOICES)
    estimated_main_minutes = models.PositiveIntegerField()
    play_goal = models.CharField(max_length=240, blank=True, default='')
    release_year = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    cover_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['platform__generation__retro_sort_order', 'platform__sort_order',
                    'sort_order', 'release_year', 'activity__name', 'id']
        constraints = [
            models.CheckConstraint(condition=Q(estimated_main_minutes__gte=1), name='retro_game_positive_estimate'),
            models.CheckConstraint(condition=Q(sort_order__gte=1), name='retro_game_positive_order'),
            models.CheckConstraint(condition=Q(tier__in=['essential', 'complementary']), name='retro_game_valid_tier'),
            models.CheckConstraint(
                condition=Q(release_year__isnull=True) | Q(release_year__range=(1970, 2100)),
                name='retro_game_valid_release_year',
            ),
        ]

    def clean(self):
        if self.activity_id and self.platform_id:
            if not self.platform.generation.group.is_retro_catalog:
                raise ValidationError({'platform': 'A plataforma deve pertencer ao grupo RetroGames.'})
            if self.activity.category_id != self.platform.generation_id:
                raise ValidationError('A Activity e a plataforma devem pertencer à mesma geração.')

    def __str__(self):
        return self.activity.name


class RetroGameProgress(models.Model):
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, 'Em andamento'),
        (STATUS_COMPLETED, 'Concluído'),
        (STATUS_SKIPPED, 'Pulado'),
    ]

    scope_key = models.CharField(max_length=64, db_index=True)
    retro_game = models.ForeignKey(RetroGame, on_delete=models.PROTECT, related_name='progress_records')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['scope_key', 'retro_game'], name='retro_progress_scope_game_unique'),
            models.CheckConstraint(condition=Q(version__gte=1), name='retro_progress_positive_version'),
            models.CheckConstraint(
                condition=Q(status__in=['in_progress', 'completed', 'skipped']),
                name='retro_progress_valid_status',
            ),
            models.CheckConstraint(
                condition=(Q(status='completed', completed_at__isnull=False)
                           | (~Q(status='completed') & Q(completed_at__isnull=True))),
                name='retro_progress_completion_timestamp',
            ),
        ]


class GoalActivitySkip(models.Model):
    """Immutable activity-skip fact used by weekly goal analytics."""

    CONTEXT_CHOICES = [
        ('live_skip', 'Pulo confirmado'),
        ('legacy_current', 'Contexto atual do legado'),
    ]

    source_queue_item_id = models.PositiveBigIntegerField(unique=True)
    source_queue_id = models.PositiveBigIntegerField()
    scope_key = models.CharField(max_length=64)
    activity_id_snapshot = models.PositiveBigIntegerField()
    activity_name_snapshot = models.CharField(max_length=100)
    category_id_snapshot = models.PositiveBigIntegerField()
    category_name_snapshot = models.CharField(max_length=50)
    category_color_snapshot = models.CharField(max_length=7)
    group_id_snapshot = models.PositiveBigIntegerField()
    group_name_snapshot = models.CharField(max_length=50)
    group_color_snapshot = models.CharField(max_length=7)
    queue_mode_snapshot = models.CharField(max_length=24)
    skipped_at = models.DateTimeField()
    context_source = models.CharField(max_length=16, choices=CONTEXT_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['scope_key', 'skipped_at'], name='goal_skip_scope_time_idx'),
            models.Index(
                fields=['scope_key', 'group_id_snapshot', 'skipped_at'],
                name='goal_skip_group_time_idx',
            ),
            models.Index(
                fields=['scope_key', 'category_id_snapshot', 'skipped_at'],
                name='goal_skip_category_time_idx',
            ),
        ]
