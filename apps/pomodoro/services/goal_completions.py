"""Immutable facts shared by live completion and the explicit legacy backfill."""

from apps.pomodoro.models import GoalCompletion, History, Schedule


def completion_values(schedule):
    if schedule.state != Schedule.STATE_COMPLETED or not schedule.completed:
        return None, 'not_completed'
    if not schedule.scope_key or schedule.scope_key == 'anonymous':
        return None, 'missing_scope'
    try:
        history = schedule.execution_history
    except History.DoesNotExist:
        return None, 'missing_history'
    if (not schedule.completed_at or not history.end_time
            or schedule.completed_at != history.end_time):
        return None, 'invalid_completion_time'
    if history.end_time < history.start_time:
        return None, 'invalid_interval'
    if history.duration is None or history.duration < 0:
        return None, 'invalid_duration'
    if history.activity_id != schedule.activity_id:
        return None, 'activity_mismatch'
    has_snapshot = bool(schedule.goal_category_id_snapshot and schedule.goal_group_id_snapshot)
    category = None if has_snapshot else schedule.activity.category
    return {
        'scope_key': schedule.scope_key,
        'category_id_snapshot': schedule.goal_category_id_snapshot if has_snapshot else category.pk,
        'group_id_snapshot': schedule.goal_group_id_snapshot if has_snapshot else category.group_id,
        'completed_at': schedule.completed_at,
        'duration_minutes': history.duration,
        'activity_id_snapshot': schedule.activity_id,
        'activity_name_snapshot': schedule.activity.name,
        'started_at': history.start_time,
        'duration_seconds': max(int((history.end_time - history.start_time).total_seconds()), 0),
        'execution_origin': schedule.execution_origin,
        'context_source': 'execution_start' if has_snapshot else 'legacy_current',
    }, None


def record_completion(schedule):
    """Caller holds the schedule lock and transaction. Existing facts never change."""
    if GoalCompletion.objects.filter(source_schedule_id=schedule.pk).exists():
        return 'existing'
    values, reason = completion_values(schedule)
    if reason:
        return reason
    _, created = GoalCompletion.objects.get_or_create(
        source_schedule_id=schedule.pk, defaults=values,
    )
    return 'inserted' if created else 'existing'
