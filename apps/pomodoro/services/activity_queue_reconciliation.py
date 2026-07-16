from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Protocol

from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.pomodoro.models import Activity, ActivityQueue, ActivityQueueItem, History, Schedule
from apps.pomodoro.services.activity_queue import (
    activity_is_eligible,
    category_started_count,
    group_remaining_minutes,
)


logger = logging.getLogger(__name__)


class RandomSource(Protocol):
    def shuffle(self, values: list[object]) -> None: ...


@dataclass(frozen=True)
class ReconciliationResult:
    queue_id: int
    inserted: int = 0
    promoted: int = 0
    demoted: int = 0
    positions_written: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.positions_written)


@dataclass
class ReconciliationSummary:
    queues_checked: int = 0
    items_inserted: int = 0
    items_promoted: int = 0
    items_demoted: int = 0
    errors: int = 0
    failed_queue_ids: list[int] = field(default_factory=list)

    def add(self, result: ReconciliationResult) -> None:
        self.items_inserted += result.inserted
        self.items_promoted += result.promoted
        self.items_demoted += result.demoted

    def as_dict(self) -> dict[str, object]:
        return {
            'queues_checked': self.queues_checked,
            'items_inserted': self.items_inserted,
            'items_promoted': self.items_promoted,
            'items_demoted': self.items_demoted,
            'errors': self.errors,
            'failed_queue_ids': self.failed_queue_ids,
        }


def activity_snapshot(activity: Activity) -> dict[str, object]:
    return {
        'active': activity.active,
        'category_id': activity.category_id,
        'group_id': activity.category.group_id if activity.category_id else None,
        'duration': activity.duration,
        'premium': activity.premium,
        'premium_from': activity.premium_from,
        'premium_until': activity.premium_until,
        'is_premium_active': activity.is_premium_active,
    }


def _premium_is_operationally_eligible(activity: Activity, queue: ActivityQueue) -> bool:
    if not activity.is_premium_active or not activity.active or not activity.category_id:
        return False
    if category_started_count(activity.category) >= activity.category.max_daily_executions:
        return False
    if History.objects.filter(
        activity=activity,
        end_time__date=timezone.localdate(),
    ).exists():
        return False
    remaining = group_remaining_minutes(queue.group)
    if remaining is not None and activity.duration > remaining:
        return False
    return not Schedule.objects.filter(
        activity=activity,
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
    ).exists()


def _eligible_premiums(queue: ActivityQueue) -> list[Activity]:
    candidates = Activity.objects.select_related('category__group').filter(
        active=True,
        premium=True,
        premium_from__lte=timezone.localdate(),
        premium_until__gte=timezone.localdate(),
        category__isnull=False,
    ).order_by('id')
    return [
        activity
        for activity in candidates
        if _premium_is_operationally_eligible(activity, queue)
    ]


def _rewrite_pending_region(
    queue: ActivityQueue,
    *,
    existing_pending: list[ActivityQueueItem],
    desired: list[ActivityQueueItem | Activity],
) -> int:
    missing_activities = [value for value in desired if isinstance(value, Activity)]
    maximum = queue.items.aggregate(value=Max('position'))['value'] or 0
    final_positions = sorted(item.position for item in existing_pending)
    final_positions.extend(range(maximum + 1, maximum + len(missing_activities) + 1))
    temporary_start = maximum + len(existing_pending) + len(missing_activities) + 1

    for offset, item in enumerate(existing_pending):
        item.position = temporary_start + offset
    if existing_pending:
        ActivityQueueItem.objects.bulk_update(existing_pending, ['position'])

    created = [
        ActivityQueueItem(
            queue=queue,
            activity=activity,
            position=temporary_start + len(existing_pending) + offset,
        )
        for offset, activity in enumerate(missing_activities)
    ]
    if created:
        ActivityQueueItem.objects.bulk_create(created)

    created_by_activity = {item.activity_id: item for item in created}
    ordered_items = [
        value if isinstance(value, ActivityQueueItem) else created_by_activity[value.id]
        for value in desired
    ]
    for position, item in zip(final_positions, ordered_items, strict=True):
        item.position = position
    ActivityQueueItem.objects.bulk_update(ordered_items, ['position'])
    return len(ordered_items)


def reconcile_premium_queue(
    queue: ActivityQueue,
    *,
    rng: RandomSource = random,
) -> ReconciliationResult:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('reconcile_premium_queue exige uma transacao ativa.')

    queue = ActivityQueue.objects.select_for_update().select_related('group').get(pk=queue.pk)
    if queue.state != ActivityQueue.STATE_ACTIVE or queue.mode != ActivityQueue.MODE_NORMAL:
        return ReconciliationResult(queue_id=queue.id)

    items = list(
        queue.items.select_for_update()
        .select_related('activity__category__group')
        .order_by('position', 'id')
    )
    pending = [item for item in items if item.state == ActivityQueueItem.STATE_PENDING]
    if not pending:
        return ReconciliationResult(queue_id=queue.id)

    eligible_premiums = _eligible_premiums(queue)
    eligible_ids = {activity.id for activity in eligible_premiums}
    all_activity_ids = {item.activity_id for item in items}

    prefix: list[ActivityQueueItem] = []
    for item in pending:
        if item.activity_id not in eligible_ids:
            break
        prefix.append(item)

    prefix_ids = {item.activity_id for item in prefix}
    new_existing = [
        item for item in pending
        if item.activity_id in eligible_ids and item.activity_id not in prefix_ids
    ]
    missing = [
        activity for activity in eligible_premiums
        if activity.id not in all_activity_ids
    ]

    new_batch: list[ActivityQueueItem | Activity] = [*new_existing, *missing]
    rng.shuffle(new_batch)
    normals = [item for item in pending if item.activity_id not in eligible_ids]
    desired = [*new_batch, *prefix, *normals]
    desired_ids = [
        value.activity_id if isinstance(value, ActivityQueueItem) else value.id
        for value in desired
    ]
    current_ids = [item.activity_id for item in pending]

    if not missing and desired_ids == current_ids:
        return ReconciliationResult(queue_id=queue.id)

    last_active_premium_index = max(
        (index for index, item in enumerate(pending) if item.activity_id in eligible_ids),
        default=-1,
    )
    demoted = sum(
        1
        for index, item in enumerate(pending)
        if index < last_active_premium_index
        and item.activity.premium
        and item.activity_id not in eligible_ids
    )
    positions_written = _rewrite_pending_region(
        queue,
        existing_pending=pending,
        desired=desired,
    )
    if missing:
        queue.pool_size = queue.items.count()
        queue.save(update_fields=['pool_size'])

    return ReconciliationResult(
        queue_id=queue.id,
        inserted=len(missing),
        promoted=len(new_existing),
        demoted=demoted,
        positions_written=positions_written,
    )


def reconcile_all_premium_queues(
    *,
    rng: RandomSource = random,
    dry_run: bool = False,
) -> ReconciliationSummary:
    summary = ReconciliationSummary()
    queue_ids = list(
        ActivityQueue.objects.filter(
            state=ActivityQueue.STATE_ACTIVE,
            mode=ActivityQueue.MODE_NORMAL,
        ).order_by('id').values_list('id', flat=True)
    )

    for queue_id in queue_ids:
        summary.queues_checked += 1
        try:
            with transaction.atomic():
                result = reconcile_premium_queue(ActivityQueue(pk=queue_id), rng=rng)
                summary.add(result)
                if dry_run:
                    transaction.set_rollback(True)
        except Exception:
            logger.exception(
                'Falha ao reconciliar prioridade premium',
                extra={'queue_id': queue_id},
            )
            summary.errors += 1
            summary.failed_queue_ids.append(queue_id)
    return summary


def _insert_randomly(queue: ActivityQueue, activity: Activity) -> ActivityQueueItem:
    existing = queue.items.filter(activity=activity).first()
    if existing:
        return existing
    maximum = queue.items.aggregate(value=Max('position'))['value'] or 0
    first_unconsumed = queue.items.filter(
        state=ActivityQueueItem.STATE_PENDING
    ).order_by('position').values_list('position', flat=True).first()
    position = random.randint(first_unconsumed or maximum + 1, maximum + 1)
    tail = list(
        queue.items.filter(position__gte=position)
        .order_by('position')
        .values_list('id', 'position')
    )
    if tail:
        offset = maximum + len(tail) + 2
        queue.items.filter(position__gte=position).update(position=F('position') + offset)
        for item_id, old_position in reversed(tail):
            queue.items.filter(pk=item_id).update(position=old_position + 1)
    item = ActivityQueueItem.objects.create(
        queue=queue,
        activity=activity,
        position=position,
    )
    queue.pool_size = queue.items.count()
    queue.save(update_fields=['pool_size'])
    return item


@transaction.atomic
def reconcile_activity(activity: Activity, *, previous: dict[str, object] | None = None):
    activity = Activity.objects.select_related('category__group').get(pk=activity.pk)
    queues = list(
        ActivityQueue.objects.select_for_update()
        .select_related('group')
        .filter(state=ActivityQueue.STATE_ACTIVE, mode=ActivityQueue.MODE_NORMAL)
        .order_by('id')
    )
    changed = False
    for queue in queues:
        item = queue.items.filter(activity=activity).first()
        eligible = activity_is_eligible(activity, queue.group, allow_global_premium=True)
        if item:
            if not eligible and item.state in [
                ActivityQueueItem.STATE_PENDING,
                ActivityQueueItem.STATE_PRESENTED,
            ]:
                item.state = ActivityQueueItem.STATE_EXPIRED
                item.save(update_fields=['state'])
                changed = True
        elif eligible and not activity.is_premium_active:
            _insert_randomly(queue, activity)
            changed = True

        result = reconcile_premium_queue(queue, rng=random)
        changed = changed or result.changed
    return changed
