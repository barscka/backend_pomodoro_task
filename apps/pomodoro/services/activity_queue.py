from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone

from apps.pomodoro.models import (
    Activity,
    ActivityPreferenceEvent,
    ActivityQueue,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
)
from apps.pomodoro.services.goal_activity_skips import record_activity_skip


class QueueConflict(Exception):
    def __init__(self, code: str, detail: str, *, payload=None):
        self.code = code
        self.detail = detail
        self.payload = payload or {}
        super().__init__(detail)


@dataclass(frozen=True)
class QueuePresentationResult:
    item: ActivityQueueItem | None
    reason: str | None
    group: Group
    consumed_daily_minutes: int
    remaining_daily_minutes: int | None


@dataclass(frozen=True)
class QueueRecreationResult:
    queue: ActivityQueue
    recreated_from_queue_id: int
    requeued_skipped_count: int
    skipped_not_requeued: list[dict[str, object]]


@dataclass(frozen=True)
class QueueActivityListResult:
    queue: ActivityQueue | None
    available_count: int
    activities: list[dict[str, object]]


def group_daily_metrics(group: Group) -> dict[str, int | None]:
    consumed = group_reserved_minutes(group)
    remaining = None
    if group.max_daily_minutes:
        remaining = max(group.max_daily_minutes - consumed, 0)
    return {
        'group_max_daily_minutes': group.max_daily_minutes,
        'group_consumed_daily_minutes': consumed,
        'group_remaining_daily_minutes': remaining,
    }


def queue_context(queue: ActivityQueue) -> dict[str, object]:
    return {
        'queue_id': queue.id,
        'queue_group_id': queue.group_id,
        'queue_group_name': queue.group.name,
        **group_daily_metrics(queue.group),
    }


def default_group() -> Group:
    group = Group.objects.filter(is_default=True).order_by('id').first()
    if group:
        return group
    group, _ = Group.objects.get_or_create(
        name='Todos',
        defaults={
            'description': 'Grupo padrao que mantem o comportamento atual.',
            'color': '#FFFFFF',
        },
    )
    group.is_default = True
    group.save(update_fields=['is_default'])
    return group


def normalize_group(selected_group: Group | None) -> Group:
    return selected_group or default_group()


def activity_belongs_to_queue(activity: Activity, queue_group: Group) -> bool:
    return bool(
        activity.category_id
        and (
            queue_group.is_default
            or activity.category.group_id == queue_group.id
        )
    )


def get_requested_group(request):
    group_id = request.query_params.get('group_id') or request.data.get('group_id')
    if group_id:
        return Group.objects.filter(pk=group_id).first()

    group_name = request.query_params.get('group_name') or request.data.get('group_name')
    if group_name:
        return Group.objects.filter(name__iexact=group_name).first()
    return default_group()


def expire_finished_premiums():
    from apps.pomodoro.services.premium_periods import sync_activity_premium_projections
    sync_activity_premium_projections()


def group_reserved_minutes(group: Group, *, day=None) -> int:
    day = day or timezone.localdate()
    histories = History.objects.filter(start_time__date=day)
    histories = histories.filter(schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY])
    if not group.is_default:
        histories = histories.filter(activity__category__group=group)
    return histories.aggregate(total=Sum('activity__duration'))['total'] or 0


def group_remaining_minutes(group: Group, *, day=None) -> int | None:
    if group.max_daily_minutes == 0:
        return None
    return max(group.max_daily_minutes - group_reserved_minutes(group, day=day), 0)


def diagnose_empty_queue(group: Group) -> str:
    base = Activity.objects.filter(active=True, category__isnull=False)
    if not group.is_default:
        base = base.filter(category__group=group)
    if not base.exists():
        return 'no_activities'

    remaining = group_remaining_minutes(group)
    if remaining == 0:
        return 'group_daily_time_limit_reached'

    today = timezone.localdate()
    available_categories = Category.objects.filter(activities__in=base).annotate(
        started=Count(
            'activities__histories',
            filter=Q(activities__histories__start_time__date=today,
                     activities__histories__schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY]),
        )
    ).filter(started__lt=F('max_daily_executions'))
    if not available_categories.exists():
        return 'category_daily_limit_reached'

    candidates = base.filter(category__in=available_categories).exclude(
        id__in=History.objects.filter(end_time__date=today,
            schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY]).values('activity_id')
    )
    if remaining is not None and candidates.exists() and not candidates.filter(
        duration__lte=remaining
    ).exists():
        return 'no_activity_fits_remaining_time'
    return 'unknown'


def category_started_count(category: Category, *, day=None) -> int:
    day = day or timezone.localdate()
    return History.objects.filter(activity__category=category, start_time__date=day,
        schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY]).count()


def activity_is_eligible(
    activity: Activity,
    group: Group,
    *,
    include_done_today=False,
) -> bool:
    if not activity.active or not activity.category_id:
        return False
    if not activity_belongs_to_queue(activity, group):
        return False
    if category_started_count(activity.category) >= activity.category.max_daily_executions:
        return False
    if not include_done_today and History.objects.filter(
        activity=activity,
        end_time__date=timezone.localdate(),
        schedule__execution_origin__in=[Schedule.ORIGIN_QUEUE, Schedule.ORIGIN_LEGACY],
    ).exists():
        return False
    if activity.is_premium_active and Schedule.objects.filter(
        activity=activity,
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
    ).exists():
        return False
    remaining = group_remaining_minutes(group)
    return remaining is None or activity.duration <= remaining


def eligible_activities(*, selected_group: Group | None, include_done_today: bool = False):
    expire_finished_premiums()
    group = normalize_group(selected_group)
    today = timezone.localdate()
    queryset = Activity.objects.select_related('category', 'category__group').filter(
        active=True,
        category__isnull=False,
    )
    if not include_done_today:
        queryset = queryset.exclude(
            id__in=History.objects.filter(end_time__date=today).values('activity_id')
        )
    if not group.is_default:
        queryset = queryset.filter(category__group=group)
    exhausted = Category.objects.annotate(
        started=Count(
            'activities__histories',
            filter=Q(activities__histories__start_time__date=today),
        )
    ).filter(started__gte=F('max_daily_executions'))
    queryset = queryset.exclude(category_id__in=exhausted.values('id'))
    remaining = group_remaining_minutes(group)
    if remaining is not None:
        queryset = queryset.filter(duration__lte=remaining)
    queryset = queryset.exclude(
        Q(
            premium=True,
            premium_from__lte=today,
            premium_until__gte=today,
        )
        & Q(schedules__state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING])
    )
    return queryset.distinct()


def _favorite_weights(scope_key: str, group: Group) -> dict[int, int]:
    events = ActivityPreferenceEvent.objects.filter(
        queue__scope_key=scope_key,
        queue__group=group,
    ).order_by('created_at', 'id').values('activity_id', 'event_type')
    favorites = Counter()
    for event in events:
        if event['event_type'] == ActivityPreferenceEvent.EVENT_FAVORITE_COMPLETED:
            favorites[event['activity_id']] += 1
    return {activity_id: 4 if count else 1 for activity_id, count in favorites.items()}


def _weighted_order(activities, scope_key: str, group: Group, *, rng=random):
    weights = _favorite_weights(scope_key, group)
    ranked = []
    for activity in activities:
        weight = max(weights.get(activity.id, 1), 1)
        score = rng.random() ** (1.0 / weight)
        ranked.append((not activity.is_premium_active, -score, activity.id, activity))
    ranked.sort(key=lambda row: row[:3])
    return [row[3] for row in ranked]


def _next_pool_number(scope_key: str, group: Group) -> int:
    maximum = ActivityQueue.objects.filter(scope_key=scope_key, group=group).aggregate(
        value=Max('pool_number')
    )['value'] or 0
    return maximum + 1


def _available_items(queue: ActivityQueue):
    return queue.items.filter(
        state__in=[
            ActivityQueueItem.STATE_PENDING,
            ActivityQueueItem.STATE_PRESENTED,
            ActivityQueueItem.STATE_STARTED,
        ]
    )


def _refresh_queue_counters(queue: ActivityQueue):
    queue.consumed_count = queue.items.filter(
        state__in=[ActivityQueueItem.STATE_COMPLETED, ActivityQueueItem.STATE_SKIPPED]
    ).count()
    queue.pool_size = queue.items.count()
    queue.save(update_fields=['consumed_count', 'pool_size'])


def close_queue(queue: ActivityQueue):
    if queue.state == ActivityQueue.STATE_ACTIVE:
        queue.state = ActivityQueue.STATE_CLOSED
        queue.closed_at = timezone.now()
        queue.save(update_fields=['state', 'closed_at'])
    _refresh_queue_counters(queue)
    return queue


def _create_review(source_queue: ActivityQueue) -> ActivityQueue | None:
    if source_queue.mode != ActivityQueue.MODE_NORMAL:
        return None
    existing = ActivityQueue.objects.filter(source_queue=source_queue).first()
    if existing:
        return existing
    skipped = list(
        source_queue.items.select_related('activity__category__group')
        .filter(state=ActivityQueueItem.STATE_SKIPPED, activity__active=True)
        .order_by('position')
    )
    skipped = [item for item in skipped if activity_is_eligible(
        item.activity, source_queue.group, include_done_today=True
    )]
    if not skipped:
        return None
    random.shuffle(skipped)
    review = ActivityQueue.objects.create(
        group=source_queue.group,
        scope_key=source_queue.scope_key,
        mode=ActivityQueue.MODE_SKIPPED_REVIEW,
        pool_number=_next_pool_number(source_queue.scope_key, source_queue.group),
        pool_size=len(skipped),
        skip_locked=True,
        source_queue=source_queue,
    )
    ActivityQueueItem.objects.bulk_create([
        ActivityQueueItem(queue=review, activity=item.activity, position=position)
        for position, item in enumerate(skipped, start=1)
    ])
    return review


def finalize_queue_if_finished(queue: ActivityQueue) -> ActivityQueue | None:
    if _available_items(queue).exists():
        _refresh_queue_counters(queue)
        return queue
    close_queue(queue)
    if queue.mode == ActivityQueue.MODE_NORMAL:
        return _create_review(queue)
    return None


def _expire_invalid_items(queue: ActivityQueue):
    now = timezone.now()
    for item in queue.items.select_related('schedule').filter(
        state__in=[ActivityQueueItem.STATE_PENDING, ActivityQueueItem.STATE_PRESENTED, ActivityQueueItem.STATE_STARTED],
        schedule__isnull=False,
    ):
        if item.schedule.state in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
            item.state = ActivityQueueItem.STATE_STARTED
            item.started_at = item.started_at or item.schedule.starts_at or now
            item.save(update_fields=['state', 'started_at'])
        elif item.schedule.state == Schedule.STATE_COMPLETED:
            item.state = ActivityQueueItem.STATE_COMPLETED
            item.completed_at = item.completed_at or item.schedule.completed_at or now
            item.save(update_fields=['state', 'completed_at'])

    for item in queue.items.select_related('activity__category__group').filter(
        state__in=[ActivityQueueItem.STATE_PENDING, ActivityQueueItem.STATE_PRESENTED]
    ):
        if activity_is_eligible(
            item.activity,
            queue.group,
            include_done_today=queue.mode == ActivityQueue.MODE_SKIPPED_REVIEW,
        ):
            continue
        item.state = ActivityQueueItem.STATE_EXPIRED
        item.save(update_fields=['state'])
    _refresh_queue_counters(queue)


def _create_normal_queue(scope_key: str, group: Group) -> ActivityQueue | None:
    activities = _weighted_order(
        list(eligible_activities(selected_group=group)),
        scope_key,
        group,
    )
    if not activities:
        return None
    try:
        queue = ActivityQueue.objects.create(
            group=group,
            scope_key=scope_key,
            mode=ActivityQueue.MODE_NORMAL,
            pool_number=_next_pool_number(scope_key, group),
            pool_size=len(activities),
            skip_locked=False,
        )
    except IntegrityError:
        return ActivityQueue.objects.get(
            scope_key=scope_key,
            group=group,
            state=ActivityQueue.STATE_ACTIVE,
        )
    ActivityQueueItem.objects.bulk_create([
        ActivityQueueItem(queue=queue, activity=activity, position=position)
        for position, activity in enumerate(activities, start=1)
    ])
    return queue


def _ineligibility_reason(activity: Activity, group: Group) -> str | None:
    if not activity.active:
        return 'inactive'
    if not activity.category_id:
        return 'category_unavailable'
    if not activity_belongs_to_queue(activity, group):
        return 'group_mismatch'
    if category_started_count(activity.category) >= activity.category.max_daily_executions:
        return 'category_daily_limit_reached'
    if History.objects.filter(
        activity=activity,
        end_time__date=timezone.localdate(),
    ).exists():
        return 'already_completed_today'
    if activity.is_premium_active and Schedule.objects.filter(
        activity=activity,
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
    ).exists():
        return 'active_execution_conflict'
    remaining = group_remaining_minutes(group)
    if remaining is not None and activity.duration > remaining:
        return 'group_daily_minutes_reached'
    return None


@transaction.atomic
def recreate_active_queue(
    *,
    scope_key: str,
    selected_group: Group | None,
    expected_queue_id: int,
    rng=random,
) -> QueueRecreationResult:
    group = normalize_group(selected_group)
    group = Group.objects.select_for_update().get(pk=group.pk)
    queue = (
        ActivityQueue.objects.select_related('group')
        .select_for_update()
        .filter(scope_key=scope_key, group=group, state=ActivityQueue.STATE_ACTIVE)
        .first()
    )
    if queue is None:
        raise QueueConflict(
            'active_queue_not_found',
            'Nao existe fila ativa para este grupo.',
            payload={'queue_group_id': group.id, 'recoverable': True},
        )
    if queue.id != expected_queue_id:
        raise QueueConflict(
            'queue_changed',
            'A fila ativa mudou desde a ultima leitura.',
            payload={
                'queue_id': queue.id,
                'queue_group_id': group.id,
                'recoverable': True,
            },
        )

    open_schedule = (
        Schedule.objects.select_for_update()
        .filter(
            scope_key=scope_key,
            state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
        )
        .order_by('id')
        .first()
    )
    if open_schedule:
        raise QueueConflict(
            'active_execution_running',
            'Existe uma atividade em execucao neste escopo.',
            payload={
                'queue_id': queue.id,
                'queue_group_id': group.id,
                'recoverable': True,
            },
        )
    if queue.mode != ActivityQueue.MODE_NORMAL:
        raise QueueConflict(
            'queue_recreation_locked',
            'A revisao obrigatoria de atividades puladas nao pode ser recriada.',
            payload={
                'queue_id': queue.id,
                'queue_group_id': group.id,
                'recoverable': True,
            },
        )

    locked_items = list(
        queue.items.select_related('activity__category__group')
        .select_for_update()
        .order_by('position')
    )
    skipped_activities = {
        item.activity_id: item.activity
        for item in locked_items
        if item.state == ActivityQueueItem.STATE_SKIPPED
    }
    skipped_not_requeued = []
    eligible_skipped_ids = set()
    for activity_id, activity in skipped_activities.items():
        reason = _ineligibility_reason(activity, group)
        if reason:
            skipped_not_requeued.append({
                'activity_id': activity_id,
                'reason': reason,
            })
        else:
            eligible_skipped_ids.add(activity_id)

    candidates_by_id = {
        activity.id: activity
        for activity in eligible_activities(selected_group=group)
    }
    for activity_id in eligible_skipped_ids:
        candidates_by_id.setdefault(activity_id, skipped_activities[activity_id])
    ordered_activities = _weighted_order(
        list(candidates_by_id.values()),
        scope_key,
        group,
        rng=rng,
    )
    if not ordered_activities:
        raise QueueConflict(
            'no_activity_available',
            'Nenhuma atividade pode compor a nova fila.',
            payload={
                'queue_id': queue.id,
                'queue_group_id': group.id,
                'recoverable': True,
            },
        )

    queue.items.filter(
        state__in=[
            ActivityQueueItem.STATE_PENDING,
            ActivityQueueItem.STATE_PRESENTED,
        ]
    ).update(state=ActivityQueueItem.STATE_EXPIRED)
    queue.state = ActivityQueue.STATE_CANCELLED
    queue.closed_at = timezone.now()
    queue.save(update_fields=['state', 'closed_at'])

    new_queue = ActivityQueue.objects.create(
        group=group,
        scope_key=scope_key,
        mode=ActivityQueue.MODE_NORMAL,
        pool_number=_next_pool_number(scope_key, group),
        pool_size=len(ordered_activities),
        skip_locked=False,
        recreated_from=queue,
    )
    ActivityQueueItem.objects.bulk_create([
        ActivityQueueItem(queue=new_queue, activity=activity, position=position)
        for position, activity in enumerate(ordered_activities, start=1)
    ])
    return QueueRecreationResult(
        queue=new_queue,
        recreated_from_queue_id=queue.id,
        requeued_skipped_count=len(eligible_skipped_ids),
        skipped_not_requeued=skipped_not_requeued,
    )


def list_active_queue_activities(
    *,
    scope_key: str,
    selected_group: Group | None,
    limit: int = 30,
) -> QueueActivityListResult:
    group = normalize_group(selected_group)
    queue = (
        ActivityQueue.objects.select_related('group')
        .filter(scope_key=scope_key, group=group, state=ActivityQueue.STATE_ACTIVE)
        .first()
    )
    if queue is None:
        return QueueActivityListResult(queue=None, available_count=0, activities=[])

    operational_states = [
        ActivityQueueItem.STATE_PRESENTED,
        ActivityQueueItem.STATE_STARTED,
        ActivityQueueItem.STATE_PENDING,
    ]
    operational_items = queue.items.filter(
        state__in=operational_states,
    ).select_related('activity__category').order_by('position')
    available_count = operational_items.count()
    items = list(operational_items[:limit])
    activity_ids = {item.activity_id for item in items}

    execution_statistics = {
        row['activity_id']: row
        for row in (
            History.objects.filter(
                activity_id__in=activity_ids,
                end_time__isnull=False,
                schedule__state=Schedule.STATE_COMPLETED,
            )
            .values('activity_id')
            .annotate(execution_count=Count('id'), last_execution_at=Max('end_time'))
        )
    }
    skip_statistics = {
        row['activity_id']: row['skip_count']
        for row in (
            ActivityPreferenceEvent.objects.filter(
                activity_id__in=activity_ids,
                event_type=ActivityPreferenceEvent.EVENT_SKIPPED,
            )
            .values('activity_id')
            .annotate(skip_count=Count('id'))
        )
    }
    activities = []
    for item in items:
        execution = execution_statistics.get(item.activity_id, {})
        activities.append({
            'queue_item_id': item.id,
            'position': item.position,
            'state': item.state,
            'activity_id': item.activity_id,
            'name': item.activity.name,
            'category': {
                'id': item.activity.category_id,
                'name': item.activity.category.name,
            },
            'execution_count': execution.get('execution_count', 0),
            'last_execution_at': execution.get('last_execution_at'),
            'skip_count': skip_statistics.get(item.activity_id, 0),
        })
    return QueueActivityListResult(
        queue=queue,
        available_count=available_count,
        activities=activities,
    )


@transaction.atomic
def get_or_create_active_queue(*, scope_key: str, selected_group: Group | None):
    group = normalize_group(selected_group)
    queue = ActivityQueue.objects.select_for_update().filter(
        scope_key=scope_key,
        group=group,
        state=ActivityQueue.STATE_ACTIVE,
    ).first()
    if queue:
        _expire_invalid_items(queue)
        if queue.mode == ActivityQueue.MODE_NORMAL:
            from apps.pomodoro.services.activity_queue_reconciliation import (
                reconcile_premium_queue,
            )

            reconcile_premium_queue(queue, rng=random)
        next_queue = finalize_queue_if_finished(queue)
        if next_queue:
            return next_queue
    return _create_normal_queue(scope_key, group)


@transaction.atomic
def present_next_item(*, scope_key: str, selected_group: Group | None) -> QueuePresentationResult:
    group = normalize_group(selected_group)
    for _attempt in range(3):
        queue = get_or_create_active_queue(scope_key=scope_key, selected_group=group)
        if not queue:
            metrics = group_daily_metrics(group)
            return QueuePresentationResult(
                item=None,
                reason=diagnose_empty_queue(group),
                group=group,
                consumed_daily_minutes=metrics['group_consumed_daily_minutes'],
                remaining_daily_minutes=metrics['group_remaining_daily_minutes'],
            )
        item = queue.items.select_related('queue__group', 'activity__category__group').filter(
            state__in=[ActivityQueueItem.STATE_PRESENTED, ActivityQueueItem.STATE_STARTED]
        ).order_by('position').first()
        if item:
            metrics = group_daily_metrics(group)
            return QueuePresentationResult(
                item=item,
                reason=None,
                group=group,
                consumed_daily_minutes=metrics['group_consumed_daily_minutes'],
                remaining_daily_minutes=metrics['group_remaining_daily_minutes'],
            )
        item = queue.items.select_related('queue__group', 'activity__category__group').filter(
            state=ActivityQueueItem.STATE_PENDING
        ).order_by('position').first()
        if item:
            item.state = ActivityQueueItem.STATE_PRESENTED
            item.presented_at = timezone.now()
            item.save(update_fields=['state', 'presented_at'])
            metrics = group_daily_metrics(group)
            return QueuePresentationResult(
                item=item,
                reason=None,
                group=group,
                consumed_daily_minutes=metrics['group_consumed_daily_minutes'],
                remaining_daily_minutes=metrics['group_remaining_daily_minutes'],
            )
        finalize_queue_if_finished(queue)
    metrics = group_daily_metrics(group)
    return QueuePresentationResult(
        item=None,
        reason='unknown',
        group=group,
        consumed_daily_minutes=metrics['group_consumed_daily_minutes'],
        remaining_daily_minutes=metrics['group_remaining_daily_minutes'],
    )


@transaction.atomic
def skip_item(*, queue_item_id: int, scope_key: str):
    item = ActivityQueueItem.objects.select_related(
        'queue', 'activity__category__group',
    ).select_for_update().get(
        pk=queue_item_id
    )
    queue = item.queue
    if queue.scope_key != scope_key:
        raise QueueConflict('queue_item_not_found', 'O item da fila nao pertence ao escopo atual.')
    if queue.skip_locked:
        raise QueueConflict(
            'skip_locked',
            'Esta pool permite apenas executar atividades puladas.',
            payload={**queue_context(queue), 'queue_item_id': item.id, 'recoverable': False},
        )
    if item.state == ActivityQueueItem.STATE_STARTED:
        raise QueueConflict('active_execution_running', 'A atividade atual ja esta em execucao.')
    if item.state == ActivityQueueItem.STATE_SKIPPED:
        return item
    if item.state == ActivityQueueItem.STATE_COMPLETED:
        raise QueueConflict(
            'queue_item_consumed',
            'O item da fila ja foi consumido.',
            payload={**queue_context(queue), 'queue_item_id': item.id, 'recoverable': True},
        )
    if item.state == ActivityQueueItem.STATE_EXPIRED:
        raise QueueConflict(
            'queue_item_expired',
            'O item expirou e a fila deve ser atualizada.',
            payload={**queue_context(queue), 'queue_item_id': item.id, 'recoverable': True},
        )

    item.state = ActivityQueueItem.STATE_SKIPPED
    item.skipped_at = timezone.now()
    item.save(update_fields=['state', 'skipped_at'])
    ActivityPreferenceEvent.objects.get_or_create(
        activity=item.activity,
        queue=queue,
        queue_item=item,
        event_type=ActivityPreferenceEvent.EVENT_SKIPPED,
        defaults={'weight_delta': 1},
    )
    record_activity_skip(item)
    finalize_queue_if_finished(queue)
    return item
