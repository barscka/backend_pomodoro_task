from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path, reverse

from .models import Activity, ActivityQueue, ActivityQueueItem, Category, Group, History, Schedule
from .services.activity_queue_reconciliation import activity_snapshot, reconcile_activity
from .services.steam_import import SteamImportError, import_steam_games


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
    change_list_template = 'admin/pomodoro/activity/change_list.html'
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
    readonly_fields = ('external_source', 'external_id')

    def get_urls(self):
        custom_urls = [
            path(
                'importar-jogos-steam/',
                self.admin_site.admin_view(self.import_steam_games_view),
                name='pomodoro_activity_import_steam',
            ),
        ]
        return custom_urls + super().get_urls()

    def import_steam_games_view(self, request):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        if not self.has_add_permission(request) or not self.has_change_permission(request):
            raise PermissionDenied

        try:
            result = import_steam_games()
        except SteamImportError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        except Exception:
            self.message_user(
                request,
                'Não foi possível concluir a importação da Steam.',
                level=messages.ERROR,
            )
        else:
            level = messages.SUCCESS if result.errors == 0 else messages.WARNING
            self.message_user(
                request,
                (
                    f'Steam: {result.total} jogos encontrados; '
                    f'{result.created} criados; {result.updated} atualizados; '
                    f'{result.skipped} ignorados; {result.errors} erros.'
                ),
                level=level,
            )

        return redirect(reverse('admin:pomodoro_activity_changelist'))

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
