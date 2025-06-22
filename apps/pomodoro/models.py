from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError

class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#FFFFFF')
    max_daily_executions = models.PositiveIntegerField(default=2)  # Novo campo para o limite
    executions_today = models.PositiveIntegerField(default=0)
    
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
        ordering = ['name']
    
    def clean(self):
        """Validação temporária usando executions_today"""
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