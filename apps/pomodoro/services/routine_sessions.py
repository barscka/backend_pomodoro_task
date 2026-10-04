"""Automatic start-time association and read-only agenda session presentation."""
from collections import defaultdict
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.pomodoro.models import GameplayTrackingSettings, RoutinePlan, RoutineSessionAssociation
from apps.pomodoro.repositories.routines import associations_for_window, completion_facts_for
from .routines import expand, midnight

PAGE_SIZE = 50


def lock_plan_for_start(scope_key):
    # Always before canonical item/period/game locks: routines/start already uses
    # plan -> item. Locking the plan afterwards would introduce a deadlock cycle.
    return RoutinePlan.objects.select_for_update().filter(scope_key=scope_key).first()


def is_gameplay(schedule):
    if schedule.execution_origin in ['premium_direct', 'retro_direct'] or schedule.premium_period_id:
        return True
    config = GameplayTrackingSettings.objects.filter(scope_key=schedule.scope_key).first()
    return bool(config and config.groups.filter(pk=schedule.goal_group_id_snapshot).exists())


def associate_started_session(schedule, *, plan):
    """Called only for NEW executions, inside their canonical start transaction.

    Never repair/reassociate a replay or a legacy record. The schedule's starts_at
    is authoritative; neither requested_at nor the clock at a later retry is used.
    """
    if not plan or not schedule.starts_at or not is_gameplay(schedule):
        return None
    local_day = schedule.starts_at.astimezone(ZoneInfo(plan.timezone)).date()
    row = next((r for r in expand(schedule.scope_key, local_day, local_day)['occurrences']
                if r['kind'] == 'gameplay' and r['starts_at'] <= schedule.starts_at < r['ends_at']), None)
    if not row:
        return None
    association, _ = RoutineSessionAssociation.objects.get_or_create(source_schedule_id=schedule.id, defaults={
        'schedule': schedule, 'scope_key': schedule.scope_key, 'block_id': row['block_id'],
        'origin_date': date.fromisoformat(row['origin_date']), 'revision_version': row['revision_version'], 'timezone': plan.timezone,
        'occurrence_starts_at': row['starts_at'], 'occurrence_ends_at': row['ends_at'], 'profile': row['profile'],
        'activity_id_snapshot': schedule.activity_id, 'activity_name_snapshot': schedule.activity.name,
        'execution_origin': schedule.execution_origin, 'session_starts_at': schedule.starts_at,
    })
    return association


def occurrence_snapshot(association):
    return {'occurrence_id': f'{association.origin_date}:{association.block_id}', 'block_id': str(association.block_id),
            'origin_date': association.origin_date.isoformat(), 'revision_version': association.revision_version,
            'timezone': association.timezone, 'kind': 'gameplay', 'profile': association.profile,
            'starts_at': association.occurrence_starts_at, 'ends_at': association.occurrence_ends_at}


def execution_occurrence(schedule):
    try:
        association = schedule.routine_association
    except RoutineSessionAssociation.DoesNotExist:
        return None
    return occurrence_snapshot(association)


def session_payload(association, fact, as_of):
    schedule = association.schedule
    precise = fact is not None and fact.started_at is not None and fact.duration_seconds is not None
    confirmed = fact.duration_seconds if precise else 0
    estimate = 0
    if schedule and schedule.state in ['preparing', 'running']:
        estimate = max(0, int((min(as_of, schedule.expected_end_at or as_of) - association.session_starts_at).total_seconds()))
    return {'execution_id': association.source_schedule_id,
            'activity_id': association.activity_id_snapshot, 'activity_name': association.activity_name_snapshot,
            'execution_origin': association.execution_origin,
            'state': schedule.state if schedule else 'completed' if fact else 'unavailable',
            'starts_at': association.session_starts_at,
            'ends_at': fact.completed_at if fact else schedule.completed_at if schedule else None,
            'expected_end_at': schedule.expected_end_at if schedule else None,
            'confirmed_seconds': confirmed, 'open_estimate_seconds': estimate,
            'coverage': 'complete' if precise or (schedule and schedule.state in ['preparing', 'running']) else 'insufficient',
            'routine_occurrence': occurrence_snapshot(association),
            'routine_requested_occurrence': association.requested_occurrence}


def agenda(scope, date_from, date_to, *, sessions_page=1):
    result = expand(scope, date_from, date_to)
    start, end = midnight(date_from), midnight(date_to + timedelta(days=1))
    associations = list(associations_for_window(scope, start, end, date_from, date_to))
    facts = {f.source_schedule_id: f for f in completion_facts_for(scope, [a.source_schedule_id for a in associations])}
    as_of = timezone.now()
    grouped = defaultdict(list)
    for association in associations:
        grouped[f'{association.origin_date}:{association.block_id}'].append(session_payload(association, facts.get(association.source_schedule_id), as_of))
    recorded = []
    per_occurrence = {}
    for identity, sessions in grouped.items():
        snapshots = []
        for session in sessions:
            if session['routine_occurrence'] not in snapshots:
                snapshots.append(session['routine_occurrence'])
        details = {'sessions': sessions[(sessions_page - 1) * PAGE_SIZE:sessions_page * PAGE_SIZE],
                   'sessions_pagination': {'page': sessions_page, 'page_size': PAGE_SIZE, 'count': len(sessions),
                                           'has_next': len(sessions) > sessions_page * PAGE_SIZE},
                   'session_totals': {'confirmed_seconds': sum(s['confirmed_seconds'] for s in sessions),
                                      'open_estimate_seconds': sum(s['open_estimate_seconds'] for s in sessions)}}
        per_occurrence[identity] = details
        recorded.append({'occurrence_id': identity, 'block_id': snapshots[0]['block_id'], 'origin_date': snapshots[0]['origin_date'],
                         'snapshots': snapshots, **details})
    empty = {'sessions': [], 'sessions_pagination': {'page': sessions_page, 'page_size': PAGE_SIZE, 'count': 0, 'has_next': False},
             'session_totals': {'confirmed_seconds': 0, 'open_estimate_seconds': 0}}
    for row in result['occurrences']:
        row.update(per_occurrence.get(row['occurrence_id'], empty))
    result.update(as_of=as_of, recorded_occurrences=recorded)
    return result
