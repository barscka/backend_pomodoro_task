from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.pomodoro.models import (
    Activity,
    ActivityPreferenceEvent,
    ActivityQueueItem,
    History,
    Schedule,
)


class ActivityExecutionConflict(Exception):
    def __init__(self, code: str, detail: str, schedule: Schedule | None = None):
        self.code = code
        self.detail = detail
        self.schedule = schedule
        super().__init__(detail)


def build_scope_key(request) -> str:
    authorization = (request.META.get('HTTP_AUTHORIZATION') or '').strip()
    if not authorization:
        return 'anonymous'
    return hashlib.sha256(authorization.encode('utf-8')).hexdigest()


def get_active_schedule(scope_key: str) -> Schedule | None:
    schedule = (
        Schedule.objects.select_related(
            'activity__category__group',
            'queue_item__queue',
        )
        .filter(scope_key=scope_key, state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING])
        .order_by('-created_at')
        .first()
    )
    if not schedule:
        return None
    schedule = reconcile_schedule(schedule)
    if schedule.state not in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
        return None
    return schedule


def reconcile_schedule(schedule: Schedule) -> Schedule:
    if schedule.state not in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
        return schedule

    if schedule.expected_end_at and schedule.expected_end_at <= timezone.now():
        return complete_schedule(schedule)
    return schedule


@transaction.atomic
def start_activity(
    *,
    activity: Activity,
    queue_item: ActivityQueueItem,
    scope_key: str,
) -> tuple[Schedule, bool]:
    now = timezone.now()
    queue_item = (
        ActivityQueueItem.objects.select_related('queue', 'activity')
        .select_for_update()
        .get(pk=queue_item.pk)
    )

    if queue_item.activity_id != activity.id:
        raise ActivityExecutionConflict(
            code='queue_item_mismatch',
            detail='O item da fila informado nao corresponde a atividade solicitada.',
        )

    if queue_item.state in [ActivityQueueItem.STATE_SKIPPED, ActivityQueueItem.STATE_COMPLETED]:
        raise ActivityExecutionConflict(
            code='queue_item_unavailable',
            detail='O item da fila nao pode mais ser iniciado.',
        )

    existing = (
        Schedule.objects.select_related('activity__category__group', 'queue_item__queue')
        .select_for_update()
        .filter(scope_key=scope_key, state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING])
        .order_by('-created_at')
        .first()
    )
    if existing:
        existing = reconcile_schedule(existing)
        if existing.state in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
            if existing.queue_item_id == queue_item.id:
                return existing, False
            raise ActivityExecutionConflict(
                code='active_execution_conflict',
                detail='Ja existe uma atividade em execucao.',
                schedule=existing,
            )

    existing_same = (
        Schedule.objects.select_related('activity__category__group', 'queue_item__queue')
        .filter(queue_item=queue_item)
        .order_by('-created_at')
        .first()
    )
    if existing_same and existing_same.state in [Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]:
        return existing_same, False

    expected_end_at = now + timedelta(minutes=activity.duration)
    queue_item.state = ActivityQueueItem.STATE_STARTED
    queue_item.presented_at = queue_item.presented_at or now
    queue_item.started_at = now
    queue_item.save(update_fields=['state', 'presented_at', 'started_at'])

    try:
        schedule = Schedule.objects.create(
            activity=activity,
            scheduled_date=now.date(),
            start_time=now.time(),
            completed=False,
            queue_item=queue_item,
            scope_key=scope_key,
            state=Schedule.STATE_RUNNING,
            version=1,
            requested_at=now,
            starts_at=now,
            expected_end_at=expected_end_at,
        )
    except IntegrityError:
        conflicting = get_active_schedule(scope_key)
        if conflicting and conflicting.queue_item_id == queue_item.id:
            return conflicting, False
        raise

    History.objects.create(
        activity=activity,
        schedule=schedule,
        start_time=now,
    )
    return schedule, True


@transaction.atomic
def complete_schedule(schedule: Schedule) -> Schedule:
    schedule = (
        Schedule.objects.select_related('activity', 'execution_history', 'queue_item__queue')
        .select_for_update()
        .get(pk=schedule.pk)
    )
    if schedule.state == Schedule.STATE_COMPLETED or schedule.completed:
        return schedule

    now = timezone.now()
    completion_time = min(now, schedule.expected_end_at) if schedule.expected_end_at else now
    history = schedule.execution_history
    history.end_time = completion_time
    history.duration = max(int((completion_time - history.start_time).total_seconds() // 60), 0)
    history.save(update_fields=['end_time', 'duration'])

    schedule.end_time = completion_time.time()
    schedule.completed = True
    schedule.state = Schedule.STATE_COMPLETED
    schedule.completed_at = completion_time
    schedule.version += 1
    schedule.save(
        update_fields=['end_time', 'completed', 'state', 'completed_at', 'version']
    )

    if schedule.queue_item_id:
        queue_item = schedule.queue_item
        queue_item.state = ActivityQueueItem.STATE_COMPLETED
        queue_item.completed_at = completion_time
        queue_item.save(update_fields=['state', 'completed_at'])

        queue = queue_item.queue
        queue.consumed_count = queue.items.filter(
            state__in=[ActivityQueueItem.STATE_COMPLETED, ActivityQueueItem.STATE_SKIPPED]
        ).count()
        queue.save(update_fields=['consumed_count'])

        event_type = ActivityPreferenceEvent.EVENT_FAVORITE_COMPLETED
        if queue.mode == queue.MODE_SKIPPED_REVIEW:
            event_type = ActivityPreferenceEvent.EVENT_SKIPPED_COMPLETED
        ActivityPreferenceEvent.objects.get_or_create(
            activity=schedule.activity,
            queue=queue,
            queue_item=queue_item,
            event_type=event_type,
            defaults={'weight_delta': 1},
        )

    return schedule
