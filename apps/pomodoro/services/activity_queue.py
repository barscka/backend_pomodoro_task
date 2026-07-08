from __future__ import annotations

import random
from collections import Counter

from django.db import transaction
from django.db.models import Count, F, Max, Q
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


class QueueConflict(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def get_requested_group(request):
    group_id = request.query_params.get('group_id') or request.data.get('group_id')
    if group_id:
        return Group.objects.filter(pk=group_id).first()

    group_name = request.query_params.get('group_name') or request.data.get('group_name')
    if group_name:
        return Group.objects.filter(name__iexact=group_name).first()
    return None


def expire_finished_premiums():
    today = timezone.localdate()
    Activity.objects.filter(
        premium=True,
        premium_until__lt=today,
    ).update(premium=False)


def eligible_activities(*, selected_group: Group | None, include_done_today: bool = False):
    expire_finished_premiums()
    today = timezone.localdate()

    queryset = Activity.objects.select_related('category', 'category__group').filter(
        active=True,
        category__isnull=False,
    )

    if not include_done_today:
        completed_today = HistoryActivities.today_completed_ids(today)
        queryset = queryset.exclude(id__in=completed_today)

    if selected_group and not selected_group.is_default:
        queryset = queryset.filter(category__group=selected_group)

    exhausted_categories = list(
        Category.objects.annotate(
            executions_today_count=Count(
                'activities__histories',
                filter=Q(activities__histories__start_time__date=today),
            ),
        )
        .filter(executions_today_count__gte=F('max_daily_executions'))
        .values_list('id', flat=True)
    )
    if exhausted_categories:
        queryset = queryset.exclude(category_id__in=exhausted_categories)

    return queryset


class HistoryActivities:
    @staticmethod
    def today_completed_ids(today):
        return History.objects.filter(end_time__date=today).values_list('activity_id', flat=True)


def _favorite_weights(scope_key: str) -> dict[int, int]:
    events = list(
        ActivityPreferenceEvent.objects.filter(queue__scope_key=scope_key)
        .order_by('created_at', 'id')
        .values('activity_id', 'event_type')
    )
    favorites = Counter()
    recent_favorites = set()
    for event in events:
        if event['event_type'] == ActivityPreferenceEvent.EVENT_FAVORITE_COMPLETED:
            favorites[event['activity_id']] += 1
            recent_favorites.add(event['activity_id'])
        elif event['event_type'] == ActivityPreferenceEvent.EVENT_SKIPPED_COMPLETED:
            recent_favorites.add(event['activity_id'])

    weights = {}
    for activity_id, count in favorites.items():
        weights[activity_id] = 4 if count >= 1 and activity_id in recent_favorites else 2
    return weights


def _pending_skipped_ids(scope_key: str) -> set[int]:
    balance = Counter()
    events = (
        ActivityPreferenceEvent.objects.filter(queue__scope_key=scope_key)
        .order_by('created_at', 'id')
        .values('activity_id', 'event_type')
    )
    for event in events:
        if event['event_type'] == ActivityPreferenceEvent.EVENT_SKIPPED:
            balance[event['activity_id']] += 1
        elif event['event_type'] == ActivityPreferenceEvent.EVENT_SKIPPED_COMPLETED:
            balance[event['activity_id']] = max(balance[event['activity_id']] - 1, 0)
    return {activity_id for activity_id, amount in balance.items() if amount > 0}


def _weighted_order(activities, scope_key: str):
    rng = random.Random()
    weights = _favorite_weights(scope_key)
    premium = []
    normal = []
    for activity in activities:
        weight = max(weights.get(activity.id, 1), 1)
        score = rng.random() ** (1.0 / weight)
        bucket = premium if activity.is_premium_active else normal
        bucket.append((score, activity))

    premium.sort(key=lambda item: item[0], reverse=True)
    normal.sort(key=lambda item: item[0], reverse=True)
    return [activity for _score, activity in premium + normal]


def _next_mode(scope_key: str) -> tuple[str, bool, int]:
    closed_normal_count = ActivityQueue.objects.filter(
        scope_key=scope_key,
        state=ActivityQueue.STATE_CLOSED,
        mode=ActivityQueue.MODE_NORMAL,
    ).count()
    pending_skipped = _pending_skipped_ids(scope_key)
    next_pool_number = (
        ActivityQueue.objects.filter(scope_key=scope_key).aggregate(max_pool=Max('pool_number'))['max_pool'] or 0
    ) + 1
    if pending_skipped and closed_normal_count and closed_normal_count % 5 == 0:
        return ActivityQueue.MODE_SKIPPED_REVIEW, True, next_pool_number
    return ActivityQueue.MODE_NORMAL, False, next_pool_number


@transaction.atomic
def get_or_create_active_queue(*, scope_key: str, selected_group: Group | None):
    queue = (
        ActivityQueue.objects.select_for_update()
        .filter(scope_key=scope_key, state=ActivityQueue.STATE_ACTIVE)
        .first()
    )
    if queue:
        _expire_invalid_items(queue)
        if queue.items.filter(
            state__in=[
                ActivityQueueItem.STATE_PENDING,
                ActivityQueueItem.STATE_PRESENTED,
                ActivityQueueItem.STATE_STARTED,
            ]
        ).exists():
            return queue
        close_queue(queue)

    mode, skip_locked, pool_number = _next_mode(scope_key)
    include_done_today = mode == ActivityQueue.MODE_SKIPPED_REVIEW
    activities = list(eligible_activities(selected_group=selected_group, include_done_today=include_done_today))

    if mode == ActivityQueue.MODE_SKIPPED_REVIEW:
        pending_skipped = _pending_skipped_ids(scope_key)
        activities = [activity for activity in activities if activity.id in pending_skipped]
    else:
        ordered_activities = _weighted_order(activities, scope_key)
        activities = ordered_activities[:30] if len(ordered_activities) > 30 else ordered_activities

    if not activities:
        return None

    queue = ActivityQueue.objects.create(
        group=selected_group,
        scope_key=scope_key,
        state=ActivityQueue.STATE_ACTIVE,
        mode=mode,
        pool_number=pool_number,
        pool_size=len(activities),
        skip_locked=skip_locked,
    )

    items = [
        ActivityQueueItem(queue=queue, activity=activity, position=index + 1)
        for index, activity in enumerate(activities)
    ]
    ActivityQueueItem.objects.bulk_create(items)
    return queue


def _expire_invalid_items(queue: ActivityQueue):
    now = timezone.now()
    invalid_items = queue.items.select_related('activity').filter(
        state__in=[ActivityQueueItem.STATE_PENDING, ActivityQueueItem.STATE_PRESENTED],
        activity__active=False,
    )
    for item in invalid_items:
        item.state = ActivityQueueItem.STATE_EXPIRED
        item.presented_at = item.presented_at or now
        item.save(update_fields=['state', 'presented_at'])

    stale_scheduled_items = queue.items.select_related('schedule').filter(
        state__in=[
            ActivityQueueItem.STATE_PENDING,
            ActivityQueueItem.STATE_PRESENTED,
            ActivityQueueItem.STATE_STARTED,
        ],
        schedule__isnull=False,
    )
    for item in stale_scheduled_items:
        schedule = item.schedule
        if schedule.state in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
            item.state = ActivityQueueItem.STATE_STARTED
            item.started_at = item.started_at or schedule.starts_at or now
            item.save(update_fields=['state', 'started_at'])
            continue

        item.state = ActivityQueueItem.STATE_COMPLETED
        item.completed_at = item.completed_at or schedule.completed_at or now
        item.save(update_fields=['state', 'completed_at'])

    queue.consumed_count = queue.items.filter(
        state__in=[ActivityQueueItem.STATE_COMPLETED, ActivityQueueItem.STATE_SKIPPED]
    ).count()
    queue.save(update_fields=['consumed_count'])


@transaction.atomic
def present_next_item(*, scope_key: str, selected_group: Group | None):
    queue = get_or_create_active_queue(scope_key=scope_key, selected_group=selected_group)
    if not queue:
        return None

    item = (
        queue.items.select_related('activity__category__group')
        .filter(
            state__in=[ActivityQueueItem.STATE_PRESENTED, ActivityQueueItem.STATE_STARTED],
        )
        .order_by('position')
        .first()
    )
    if item:
        return item

    item = (
        queue.items.select_related('activity__category__group')
        .filter(state=ActivityQueueItem.STATE_PENDING)
        .order_by('position')
        .first()
    )
    if not item:
        close_queue(queue)
        return present_next_item(scope_key=scope_key, selected_group=selected_group)

    item.state = ActivityQueueItem.STATE_PRESENTED
    item.presented_at = timezone.now()
    item.save(update_fields=['state', 'presented_at'])
    return item


@transaction.atomic
def skip_item(*, queue_item_id: int, scope_key: str):
    item = (
        ActivityQueueItem.objects.select_related('queue', 'activity')
        .select_for_update()
        .get(pk=queue_item_id)
    )
    queue = item.queue
    if queue.scope_key != scope_key:
        raise QueueConflict('queue_item_not_found', 'O item da fila nao pertence ao escopo atual.')
    if queue.skip_locked:
        raise QueueConflict('skip_locked', 'Esta pool permite apenas executar atividades puladas.')
    if item.state == ActivityQueueItem.STATE_STARTED:
        raise QueueConflict('active_execution_running', 'A atividade atual ja esta em execucao.')
    if item.state == ActivityQueueItem.STATE_SKIPPED:
        return item
    if item.state == ActivityQueueItem.STATE_COMPLETED:
        raise QueueConflict('queue_item_completed', 'O item da fila ja foi concluido.')

    now = timezone.now()
    item.state = ActivityQueueItem.STATE_SKIPPED
    item.skipped_at = now
    item.save(update_fields=['state', 'skipped_at'])

    ActivityPreferenceEvent.objects.get_or_create(
        activity=item.activity,
        queue=queue,
        queue_item=item,
        event_type=ActivityPreferenceEvent.EVENT_SKIPPED,
        defaults={'weight_delta': 1},
    )
    queue.consumed_count = queue.items.filter(
        state__in=[ActivityQueueItem.STATE_COMPLETED, ActivityQueueItem.STATE_SKIPPED]
    ).count()
    queue.save(update_fields=['consumed_count'])
    return item


def close_queue(queue: ActivityQueue):
    if queue.state != ActivityQueue.STATE_ACTIVE:
        return queue
    queue.state = ActivityQueue.STATE_CLOSED
    queue.closed_at = timezone.now()
    queue.consumed_count = queue.items.filter(
        state__in=[ActivityQueueItem.STATE_COMPLETED, ActivityQueueItem.STATE_SKIPPED]
    ).count()
    queue.save(update_fields=['state', 'closed_at', 'consumed_count'])
    return queue
