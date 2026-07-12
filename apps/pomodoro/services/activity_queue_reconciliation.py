from __future__ import annotations

import random

from django.db import transaction
from django.db.models import F, Max

from apps.pomodoro.models import Activity, ActivityQueue, ActivityQueueItem
from apps.pomodoro.services.activity_queue import activity_is_eligible


def activity_snapshot(activity: Activity) -> dict[str, object]:
    return {
        'active': activity.active,
        'category_id': activity.category_id,
        'group_id': activity.category.group_id if activity.category_id else None,
        'duration': activity.duration,
    }


def _insert_randomly(queue: ActivityQueue, activity: Activity) -> ActivityQueueItem:
    existing = queue.items.filter(activity=activity).first()
    if existing:
        return existing
    maximum = queue.items.aggregate(value=Max('position'))['value'] or 0
    first_unconsumed = queue.items.filter(
        state=ActivityQueueItem.STATE_PENDING
    ).order_by('position').values_list('position', flat=True).first()
    position = random.randint(first_unconsumed or maximum + 1, maximum + 1)
    tail = list(queue.items.filter(position__gte=position).order_by('position').values_list('id', 'position'))
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
    )
    changed = False
    for queue in queues:
        item = queue.items.filter(activity=activity).first()
        eligible = activity_is_eligible(activity, queue.group)
        if item:
            if not eligible and item.state in [
                ActivityQueueItem.STATE_PENDING,
                ActivityQueueItem.STATE_PRESENTED,
            ]:
                item.state = ActivityQueueItem.STATE_EXPIRED
                item.save(update_fields=['state'])
                changed = True
            continue
        if eligible:
            _insert_randomly(queue, activity)
            changed = True
    return changed
