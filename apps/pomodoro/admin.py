from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path, reverse

from .models import (Activity, ActivityQueue, ActivityQueueItem, Category, Group, History,
                     Schedule, RetroGame, RetroGameProgress, RetroPlatform)
from .models import GoalActivitySkip, GoalCompletion, WeeklyGoal, WeeklyGoalRevision, PremiumPeriod, GameplayTrackingSettings, ExecutionIdempotency


class ReadOnlyGoalAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PremiumPeriod)
class PremiumPeriodAdmin(admin.ModelAdmin):
    list_display = ('activity', 'kind', 'starts_on', 'ends_on', 'ended_early_at', 'version')
    list_filter = ('kind', 'source')
    search_fields = ('activity__name', 'title')


@admin.register(GameplayTrackingSettings)
class GameplayTrackingSettingsAdmin(ReadOnlyGoalAdmin):
    list_display = ('scope_key', 'daily_reference_minutes', 'version')


@admin.register(ExecutionIdempotency)
class ExecutionIdempotencyAdmin(ReadOnlyGoalAdmin):
    list_display = ('scope_key', 'request_id', 'schedule_id_tombstone', 'created_at')


@admin.register(WeeklyGoal)
class WeeklyGoalAdmin(ReadOnlyGoalAdmin):
    list_display = ('id', 'metric', 'group', 'category', 'version')


@admin.register(WeeklyGoalRevision)
class WeeklyGoalRevisionAdmin(ReadOnlyGoalAdmin):
    list_display = ('id', 'goal', 'effective_week', 'target', 'active')


@admin.register(GoalCompletion)
class GoalCompletionAdmin(ReadOnlyGoalAdmin):
    list_display = ('source_schedule_id', 'completed_at', 'duration_minutes', 'context_source')


@admin.register(GoalActivitySkip)
class GoalActivitySkipAdmin(ReadOnlyGoalAdmin):
    list_display = ('source_queue_item_id', 'skipped_at', 'activity_name_snapshot', 'context_source')
from .services.activity_queue_reconciliation import activity_snapshot, reconcile_activity
from .services.steam_import import SteamImportError, import_steam_games


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default', 'is_retro_catalog', 'color', 'max_daily_minutes')
    list_filter = ('is_default', 'is_retro_catalog')
    search_fields = ('name',)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'retro_sort_order', 'color', 'max_daily_executions')
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

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.premium_periods.exists():
            fields.extend(['premium', 'premium_from', 'premium_until'])
        return fields

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
        if obj.premium and not obj.premium_periods.exists():
            from .services.premium_periods import create_period
            create_period(activity_id=obj.id, kind='focus', title=obj.name,
                          starts_on=obj.premium_from, ends_on=obj.premium_until,
                          source='legacy_compat')
        reconcile_activity(obj, previous=previous)


@admin.register(RetroPlatform)
class RetroPlatformAdmin(admin.ModelAdmin):
    list_display = ('name', 'generation', 'sort_order', 'release_year', 'active')
    list_filter = ('generation', 'active')
    search_fields = ('name', 'slug', 'manufacturer')
    ordering = ('generation__retro_sort_order', 'sort_order', 'release_year', 'name')
    autocomplete_fields = ('generation',)
    list_select_related = ('generation', 'generation__group')


@admin.register(RetroGame)
class RetroGameAdmin(admin.ModelAdmin):
    list_display = ('activity', 'platform', 'generation', 'tier', 'sort_order', 'active')
    list_filter = ('platform__generation', 'platform', 'tier', 'active')
    search_fields = ('activity__name', 'platform__name', 'play_goal')
    autocomplete_fields = ('activity', 'platform')
    list_select_related = ('activity', 'activity__category', 'platform', 'platform__generation')

    @admin.display(ordering='platform__generation__name')
    def generation(self, obj):
        return obj.platform.generation


@admin.register(RetroGameProgress)
class RetroGameProgressAdmin(ReadOnlyGoalAdmin):
    list_display = ('retro_game', 'status', 'version', 'started_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('retro_game__activity__name',)
    list_select_related = ('retro_game', 'retro_game__activity')

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
