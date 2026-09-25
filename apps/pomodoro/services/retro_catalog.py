from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.pomodoro.models import Category, GoalCompletion, RetroGame, RetroGameProgress, RetroPlatform, Schedule


GAME_ORDER = (
    'platform__generation__retro_sort_order',
    'platform__sort_order',
    'platform__release_year',
    'platform__name',
    'sort_order',
    'release_year',
    'activity__name',
    'id',
)


def generations():
    return Category.objects.filter(
        group__is_retro_catalog=True,
        retro_sort_order__isnull=False,
        activities__retro_game__active=True,
        activities__retro_game__platform__active=True,
        activities__active=True,
    ).distinct().select_related('group').order_by('retro_sort_order', 'name', 'id')


def platforms(*, generation_id=None):
    queryset = RetroPlatform.objects.filter(
        active=True, generation__group__is_retro_catalog=True
    ).select_related('generation')
    if generation_id:
        queryset = queryset.filter(generation_id=generation_id)
    return queryset.order_by('generation__retro_sort_order', 'sort_order', 'release_year', 'name', 'id')


def games(*, scope_key, params):
    queryset = RetroGame.objects.select_related(
        'activity', 'platform__generation__group'
    ).filter(platform__generation__group__is_retro_catalog=True)
    active = params.get('active')
    if active is None:
        queryset = queryset.filter(active=True, platform__active=True, activity__active=True)
    elif str(active).lower() in ['true', '1']:
        queryset = queryset.filter(active=True)
    elif str(active).lower() in ['false', '0']:
        queryset = queryset.filter(active=False)
    if params.get('generation_id'):
        queryset = queryset.filter(platform__generation_id=params['generation_id'])
    if params.get('platform_id'):
        queryset = queryset.filter(platform_id=params['platform_id'])
    if params.get('tier'):
        queryset = queryset.filter(tier=params['tier'])
    if params.get('search'):
        term = params['search'].strip()
        queryset = queryset.filter(Q(activity__name__icontains=term) | Q(activity__description__icontains=term)
                                    | Q(platform__name__icontains=term) | Q(play_goal__icontains=term))
    facts_for_scope = GoalCompletion.objects.filter(
        scope_key=scope_key, activity_id_snapshot=OuterRef('activity_id')
    )
    open_for_scope = Schedule.objects.filter(
        scope_key=scope_key, activity_id=OuterRef('activity_id'),
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
    )
    progress_for_scope = RetroGameProgress.objects.filter(
        scope_key=scope_key, retro_game_id=OuterRef('pk')
    )
    queryset = queryset.annotate(
        retro_has_session=Exists(facts_for_scope),
        retro_has_open_session=Exists(open_for_scope),
        retro_has_progress=Exists(progress_for_scope),
    )
    requested_status = params.get('status')
    if requested_status == 'not_started':
        queryset = queryset.exclude(progress_records__scope_key=scope_key).filter(
            retro_has_session=False, retro_has_open_session=False
        )
    elif requested_status == RetroGameProgress.STATUS_IN_PROGRESS:
        queryset = queryset.filter(
            Q(progress_records__scope_key=scope_key,
              progress_records__status=RetroGameProgress.STATUS_IN_PROGRESS)
            | (Q(retro_has_progress=False)
               & (Q(retro_has_session=True) | Q(retro_has_open_session=True)))
        )
    elif requested_status in [RetroGameProgress.STATUS_COMPLETED, RetroGameProgress.STATUS_SKIPPED]:
        queryset = queryset.filter(progress_records__scope_key=scope_key,
                                    progress_records__status=requested_status)
    return queryset.distinct().order_by(*GAME_ORDER)


def metrics_for(*, game_ids, scope_key):
    games_by_id = {game.id: game for game in RetroGame.objects.filter(id__in=game_ids)}
    activity_to_game = {game.activity_id: game.id for game in games_by_id.values()}
    facts = GoalCompletion.objects.filter(scope_key=scope_key, activity_id_snapshot__in=activity_to_game)
    totals = facts.values('activity_id_snapshot').annotate(
        played=Coalesce(Sum(Case(
            When(duration_seconds__isnull=False, then=F('duration_seconds')),
            default=F('duration_minutes') * Value(60), output_field=IntegerField(),
        )), 0)
    )
    played = {activity_to_game[row['activity_id_snapshot']]: row['played'] for row in totals}
    partial_activities = set(facts.filter(duration_seconds__isnull=True).values_list('activity_id_snapshot', flat=True))
    progresses = {p.retro_game_id: p for p in RetroGameProgress.objects.filter(
        scope_key=scope_key, retro_game_id__in=game_ids
    )}
    open_values = {}
    now = timezone.now()
    for schedule in Schedule.objects.filter(
        scope_key=scope_key,
        activity_id__in=activity_to_game,
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
    ):
        elapsed = max(int((now - (schedule.starts_at or now)).total_seconds()), 0)
        if schedule.planned_duration_seconds is not None:
            elapsed = min(elapsed, schedule.planned_duration_seconds)
        open_values[activity_to_game[schedule.activity_id]] = elapsed
    return {
        game_id: {
            'progress': progresses.get(game_id),
            'played_seconds': played.get(game_id, 0),
            'coverage': 'partial' if games_by_id[game_id].activity_id in partial_activities else 'complete',
            'open_estimate_seconds': open_values.get(game_id, 0),
        }
        for game_id in game_ids
    }
