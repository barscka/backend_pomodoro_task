from django.contrib import admin
from .models import Activity, ActivityQueue, ActivityQueueItem, Category, Group, History, Schedule
from .services.activity_queue_reconciliation import activity_snapshot, reconcile_activity


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default', 'color', 'max_daily_minutes')
    list_filter = ('is_default',)
    search_fields = ('name',)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'color', 'max_daily_executions')
    list_filter = ('group',)
    search_fields = ('name',)

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'category',
        'active',
        'premium',
        'premium_from',
        'premium_until',
        'duration',
        'executions_today',
    )
    list_filter = ('active', 'premium', 'category')
    search_fields = ('name', 'description')

    def save_model(self, request, obj, form, change):
        previous = activity_snapshot(Activity.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        reconcile_activity(obj, previous=previous)

@admin.register(History)
class HistoryAdmin(admin.ModelAdmin):
    list_display = ('activity', 'start_time', 'duration')
    list_filter = ('activity',)
    date_hierarchy = 'start_time'

@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ('activity', 'scheduled_date', 'completed')
    list_filter = ('completed', 'activity')
    date_hierarchy = 'scheduled_date'


@admin.register(ActivityQueue)
class ActivityQueueAdmin(admin.ModelAdmin):
    list_display = ('id', 'scope_key', 'group', 'mode', 'state', 'pool_size', 'consumed_count')
    list_filter = ('state', 'mode', 'group')
    readonly_fields = ('created_at', 'closed_at')


@admin.register(ActivityQueueItem)
class ActivityQueueItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'queue', 'activity', 'position', 'state')
    list_filter = ('state', 'queue__group')
