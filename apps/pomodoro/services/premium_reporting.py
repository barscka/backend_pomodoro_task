from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.pomodoro.models import GameplayTrackingSettings, GoalCompletion, PremiumPeriod, Schedule
from .premium_periods import bounds


ZONE = ZoneInfo('America/Sao_Paulo')


def _pct(a, b):
    if not b:
        return None
    return str((Decimal(a) * 100 / Decimal(b)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def settings_for(scope_key):
    obj = GameplayTrackingSettings.objects.filter(scope_key=scope_key).prefetch_related('groups').first()
    return {'daily_reference_minutes': obj.daily_reference_minutes if obj else 300,
            'group_ids': list(obj.groups.values_list('id', flat=True)) if obj else [],
            'version': obj.version if obj else 0, 'timezone': 'America/Sao_Paulo'}


def report_window(date_from, date_to):
    return (datetime.combine(date_from, time.min, ZONE), datetime.combine(date_to + timedelta(days=1), time.min, ZONE))


def completed_facts(scope_key):
    return GoalCompletion.objects.filter(scope_key=scope_key, started_at__isnull=False,
                                         duration_seconds__isnull=False).order_by('started_at')


def segments(scope_key, start, end, *, period=None):
    output = []
    periods = [period] if period else list(PremiumPeriod.objects.all())
    facts = completed_facts(scope_key)
    by_activity = defaultdict(list)
    for p in periods:
        by_activity[p.activity_id].append(p)
    for fact in facts:
        fact_end = fact.started_at + timedelta(seconds=fact.duration_seconds)
        for p in by_activity.get(fact.activity_id_snapshot, []):
            p0, p1 = bounds(p)
            s, e = max(fact.started_at, p0, start), min(fact_end, p1, end)
            if s < e:
                output.append((fact, p, s, e, int((e - s).total_seconds())))
    return output


def period_stats(period, scope_key):
    p0, p1 = bounds(period)
    now = timezone.now()
    effective_end = min(p1, now)
    rows = segments(scope_key, p0, p1, period=period)
    total = sum(row[4] for row in rows)
    days = max((effective_end.astimezone(ZONE).date() - p0.date()).days + (1 if effective_end > p0 else 0), 0)
    config = settings_for(scope_key)
    reference = days * config['daily_reference_minutes'] * 60
    open_schedule = Schedule.objects.filter(scope_key=scope_key, activity_id=period.activity_id,
        state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]).first()
    estimate = 0
    if open_schedule and open_schedule.starts_at:
        estimate = max(int((min(now, open_schedule.expected_end_at or now, p1) - max(open_schedule.starts_at, p0)).total_seconds()), 0)
    return {'period_id': period.id, 'activity_id': period.activity_id, 'consolidated_seconds': total,
            'session_count': len({r[0].source_schedule_id for r in rows}),
            'days_with_gameplay': len({r[2].astimezone(ZONE).date() for r in rows}),
            'elapsed_days': days, 'daily_reference_minutes': config['daily_reference_minutes'],
            'elapsed_reference_seconds': reference, 'reference_percent': _pct(total, reference),
            'open_estimate_seconds': estimate, 'pending_reconciliation_count': 0,
            'coverage': 'complete' if not GoalCompletion.objects.filter(scope_key=scope_key, started_at__isnull=True).exists() else 'partial',
            'as_of': now, 'timezone': period.timezone, 'period_start': p0, 'period_end_exclusive': p1}


def daily_summary(scope_key, date_from, date_to):
    start, end = report_window(date_from, date_to)
    premium, gameplay = defaultdict(int), defaultdict(int)
    for _fact, _period, seg_start, seg_end, _seconds in segments(scope_key, start, end):
        cursor = seg_start
        while cursor < seg_end:
            midnight = datetime.combine(cursor.astimezone(ZONE).date() + timedelta(days=1), time.min, ZONE)
            chunk_end = min(seg_end, midnight)
            premium[cursor.astimezone(ZONE).date()] += int((chunk_end - cursor).total_seconds())
            cursor = chunk_end
    config = settings_for(scope_key)
    if config['group_ids']:
        for fact in completed_facts(scope_key).filter(group_id_snapshot__in=config['group_ids']):
            fact_end = fact.started_at + timedelta(seconds=fact.duration_seconds)
            s, e = max(fact.started_at, start), min(fact_end, end)
            cursor = s
            while cursor < e:
                midnight = datetime.combine(cursor.astimezone(ZONE).date() + timedelta(days=1), time.min, ZONE)
                chunk_end = min(e, midnight)
                gameplay[cursor.astimezone(ZONE).date()] += int((chunk_end - cursor).total_seconds())
                cursor = chunk_end
    values, day = [], date_from
    while day <= date_to:
        values.append({'date': day, 'premium_seconds': premium[day], 'gameplay_seconds': gameplay[day],
                       'other_gameplay_seconds': max(gameplay[day] - premium[day], 0),
                       'reference_seconds': config['daily_reference_minutes'] * 60,
                       'is_partial_day': day == timezone.localdate()})
        day += timedelta(days=1)
    return values, config
