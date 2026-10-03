"""Civil-day interval intersections. Availability is never a target or debt."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.pomodoro.models import GoalCompletion, GameplayTrackingSettings, Schedule
from .routines import ZONE, expand, midnight


def overlap_seconds(start, end, left, right):
    return max(0, int((min(end, right) - max(start, left)).total_seconds()))


def capacities(scope, date_from, date_to):
    agenda = expand(scope, date_from, date_to)
    amounts_by_day = {day['date']: {'general_seconds': 0, 'interruptible_seconds': 0, 'family_seconds': 0, 'cardio_seconds': 0} for day in agenda['days']}
    for row in agenda['occurrences']:
        key = ('interruptible_seconds' if row['profile'] == 'interruptible' else 'general_seconds') if row['kind'] == 'gameplay' else row['kind'] + '_seconds'
        cursor = max(row['starts_at'], midnight(date_from))
        end = min(row['ends_at'], midnight(date_to + timedelta(days=1)))
        while cursor < end:
            day = cursor.astimezone(ZONE).date()
            chunk_end = min(end, midnight(day + timedelta(days=1)))
            amounts_by_day[day][key] += int((chunk_end - cursor).total_seconds())
            cursor = chunk_end
    values = []
    for day in agenda['days']:
        d = day['date']
        amounts = amounts_by_day[d]
        known = day['state'] == 'planned'
        values.append({'date': d, 'state': day['state'], 'known': known, **amounts,
                       'reference_seconds': amounts['general_seconds'] + amounts['interruptible_seconds'] if known else None})
    return values, agenda


def routine_reference(scope, date_from, date_to):
    values, _ = capacities(scope, date_from, date_to)
    complete = all(v['known'] for v in values)
    return {'reference_source': 'routine', 'coverage': 'complete' if complete else 'partial',
            'general_seconds': sum(v['general_seconds'] for v in values),
            'interruptible_seconds': sum(v['interruptible_seconds'] for v in values),
            'reference_seconds': sum(v['reference_seconds'] or 0 for v in values) if complete else None,
            'known_days': sum(v['known'] for v in values), 'days': values}


def gameplay_filter(scope):
    config = GameplayTrackingSettings.objects.filter(scope_key=scope).first()
    group_ids = list(config.groups.values_list('id', flat=True)) if config else []
    return Q(group_id_snapshot__in=group_ids) | Q(execution_origin__in=['premium_direct', 'retro_direct']) | Q(source_schedule_id__in=Schedule.objects.filter(scope_key=scope, premium_period__isnull=False).values('id'))


def summary(scope, date_from, date_to):
    values, agenda = capacities(scope, date_from, date_to)
    end = midnight(date_to + timedelta(days=1))
    start = midnight(date_from)
    facts = list(GoalCompletion.objects.filter(scope_key=scope).filter(gameplay_filter(scope)).filter(
        Q(started_at__lt=end, completed_at__gte=start) | Q(started_at__isnull=True, completed_at__gte=start, completed_at__lt=end)))
    open_sessions = list(Schedule.objects.filter(scope_key=scope, state__in=['preparing', 'running'], starts_at__lt=end))
    config = GameplayTrackingSettings.objects.filter(scope_key=scope).first()
    group_ids = set(config.groups.values_list('id', flat=True)) if config else set()
    missing_facts = list(Schedule.objects.filter(scope_key=scope, state='completed').filter(
        Q(execution_origin__in=['premium_direct', 'retro_direct']) | Q(premium_period__isnull=False) | Q(goal_group_id_snapshot__in=group_ids)
    ).filter(Q(completed_at__gte=start, completed_at__lt=end) | Q(completed_at__isnull=True, scheduled_date__range=(date_from, date_to)))
        .exclude(id__in=GoalCompletion.objects.filter(scope_key=scope).values('source_schedule_id')))
    now = timezone.now()
    for day in values:
        left, right = midnight(day['date']), midnight(day['date'] + timedelta(days=1))
        windows = [r for r in agenda['occurrences'] if r['kind'] == 'gameplay']
        within = outside = casual = uncovered = 0
        insufficient_count = sum((s.completed_at.astimezone(ZONE).date() if s.completed_at else s.scheduled_date) == day['date'] for s in missing_facts)
        for fact in facts:
            if fact.started_at is None or fact.duration_seconds is None:
                if left <= fact.completed_at < right:
                    insufficient_count += 1
                continue
            a, b = max(left, fact.started_at), min(right, fact.started_at + timedelta(seconds=fact.duration_seconds))
            if a >= b:
                continue
            seconds = int((b - a).total_seconds())
            if not day['known']:
                uncovered += seconds
                continue
            matched = sum(overlap_seconds(a, b, w['starts_at'], w['ends_at']) for w in windows)
            within += matched
            casual += sum(overlap_seconds(a, b, w['starts_at'], w['ends_at']) for w in windows if w['profile'] == 'interruptible')
            outside += seconds - matched
        estimate = 0
        for session in open_sessions:
            if not (session.execution_origin in ['premium_direct', 'retro_direct'] or session.premium_period_id or session.goal_group_id_snapshot in group_ids):
                continue
            estimate += overlap_seconds(left, right, session.starts_at, min(now, session.expected_end_at or now))
        day.update(confirmed_inside_seconds=within, confirmed_interruptible_seconds=casual,
                   confirmed_outside_seconds=outside, confirmed_uncovered_seconds=uncovered,
                   open_estimate_seconds=estimate, insufficient_precision_count=insufficient_count,
                   coverage='partial' if insufficient_count or not day['known'] else 'complete')
    totals = {key: sum(v[key] for v in values) for key in ('general_seconds', 'interruptible_seconds', 'family_seconds', 'cardio_seconds',
              'confirmed_inside_seconds', 'confirmed_interruptible_seconds', 'confirmed_outside_seconds', 'confirmed_uncovered_seconds',
              'open_estimate_seconds', 'insufficient_precision_count')}
    return {'timezone': str(ZONE), 'date_from': date_from, 'date_to': date_to, 'as_of': now,
            'days': values, 'totals': totals, 'coverage': 'complete' if all(v['coverage'] == 'complete' for v in values) else 'partial'}
