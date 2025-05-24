from django.contrib import admin
from .models import Category, Activity, History, Schedule

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'color')
    search_fields = ('name',)

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'duration', 'executions_today')
    list_filter = ('category',)
    search_fields = ('name', 'description')

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