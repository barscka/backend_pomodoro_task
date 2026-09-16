from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.pomodoro.models import Activity, PremiumPeriod


class PremiumPeriodConflict(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def bounds(period):
    zone = ZoneInfo(period.timezone)
    start = datetime.combine(period.starts_on, time.min, zone)
    end = datetime.combine(period.ends_on + timedelta(days=1), time.min, zone)
    if period.ended_early_at:
        end = min(end, period.ended_early_at.astimezone(zone))
    return start, end


def active_period_for_activity(activity, *, at=None):
    at = at or timezone.now()
    for period in activity.premium_periods.all().order_by('starts_on', 'id'):
        start, end = bounds(period)
        if start <= at < end:
            return period
    return None


def validate_no_overlap(activity, starts_on, ends_on, *, exclude_id=None):
    query = activity.premium_periods.filter(starts_on__lte=ends_on, ends_on__gte=starts_on)
    if exclude_id:
        query = query.exclude(pk=exclude_id)
    if query.exists():
        raise PremiumPeriodConflict('premium_period_overlap', 'O período sobrepõe outra vigência da atividade.')


@transaction.atomic
def create_period(**values):
    activity = Activity.objects.select_for_update().get(pk=values.pop('activity_id'))
    validate_no_overlap(activity, values['starts_on'], values['ends_on'])
    period = PremiumPeriod.objects.create(activity=activity, **values)
    sync_activity_premium_projection(activity)
    return period


def sync_activity_premium_projection(activity):
    current = active_period_for_activity(activity)
    future = activity.premium_periods.filter(ended_early_at__isnull=True).order_by('starts_on').first()
    projected = current or future
    values = {
        'premium': current is not None,
        'premium_from': projected.starts_on if projected else None,
        'premium_until': projected.ends_on if projected else None,
    }
    Activity.objects.filter(pk=activity.pk).update(**values)
    for key, value in values.items():
        setattr(activity, key, value)


def sync_activity_premium_projections():
    period_activity_ids = PremiumPeriod.objects.values_list('activity_id', flat=True).distinct()
    for activity in Activity.objects.prefetch_related('premium_periods').filter(
        id__in=period_activity_ids
    ):
        sync_activity_premium_projection(activity)
    # Compatibilidade até o comando de importação transformar datas legadas em períodos.
    Activity.objects.filter(premium=True, premium_periods__isnull=True,
                            premium_until__lt=timezone.localdate()).update(premium=False)
