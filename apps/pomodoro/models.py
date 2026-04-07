from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError


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
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
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
    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name='schedules'
    )
    scheduled_date = models.DateField()
    start_time = models.TimeField() 
    end_time = models.TimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('activity', 'scheduled_date')
        ordering = ['scheduled_date']

    def __str__(self):
        return f"Schedule {self.id} for {self.scheduled_date}"

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
