"""Immutable facts shared by live skips and the explicit legacy backfill."""

from apps.pomodoro.models import ActivityQueueItem, GoalActivitySkip


def skip_values(item):
    if item.state != ActivityQueueItem.STATE_SKIPPED:
        return None, 'not_skipped'
    if not item.skipped_at:
        return None, 'missing_skipped_at'
    if not item.queue.scope_key or item.queue.scope_key == 'anonymous':
        return None, 'missing_scope'
    category = item.activity.category
    if category is None or category.group_id is None:
        return None, 'missing_classification'
    group = category.group
    return {
        'source_queue_id': item.queue_id,
        'scope_key': item.queue.scope_key,
        'activity_id_snapshot': item.activity_id,
        'activity_name_snapshot': item.activity.name,
        'category_id_snapshot': category.pk,
        'category_name_snapshot': category.name,
        'category_color_snapshot': category.color,
        'group_id_snapshot': group.pk,
        'group_name_snapshot': group.name,
        'group_color_snapshot': group.color,
        'queue_mode_snapshot': item.queue.mode,
        'skipped_at': item.skipped_at,
    }, None


def record_activity_skip(item, *, context_source='live_skip'):
    """Caller holds the queue-item lock and transaction. Existing facts never change."""
    if GoalActivitySkip.objects.filter(source_queue_item_id=item.pk).exists():
        return 'existing'
    values, reason = skip_values(item)
    if reason:
        return reason
    _, created = GoalActivitySkip.objects.get_or_create(
        source_queue_item_id=item.pk,
        defaults={**values, 'context_source': context_source},
    )
    return 'inserted' if created else 'existing'
