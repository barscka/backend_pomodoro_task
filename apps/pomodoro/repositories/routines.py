"""Read-only queries: never call queue presentation or session reconciliation."""
from apps.pomodoro.models import (
    ActivityQueue, ActivityQueueItem, RoutinePlan, Schedule,
)


def plan_for(scope_key):
    return RoutinePlan.objects.filter(scope_key=scope_key).first()


def open_execution(scope_key):
    return Schedule.objects.filter(scope_key=scope_key, state__in=['preparing', 'running']).first()


def canonical_queue_item(scope_key, group_id):
    queue = ActivityQueue.objects.filter(scope_key=scope_key, group_id=group_id, state='active').first()
    if not queue:
        return None, None
    items = queue.items.select_related('activity__category__group')
    item = items.filter(state__in=[ActivityQueueItem.STATE_PRESENTED, ActivityQueueItem.STATE_STARTED]).order_by('position').first()
    if not item:
        item = items.filter(state=ActivityQueueItem.STATE_PENDING).order_by('position').first()
    return queue, item
