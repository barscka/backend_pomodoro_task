from django.db.models import Count, OuterRef, Subquery, Sum

from apps.pomodoro.models import (
    GoalActivitySkip, GoalCompletion, Schedule, WeeklyGoal, WeeklyGoalRevision,
)


def goals_for_week(scope_key, week):
    revision = WeeklyGoalRevision.objects.filter(
        goal_id=OuterRef('pk'), effective_week__lte=week,
    ).order_by('-effective_week')
    return WeeklyGoal.objects.filter(scope_key=scope_key).select_related(
        'group', 'category__group',
    ).annotate(
        target=Subquery(revision.values('target')[:1]),
        active=Subquery(revision.values('active')[:1]),
        effective_week=Subquery(revision.values('effective_week')[:1]),
    ).filter(effective_week__isnull=False).order_by('id')


def completion_totals(scope_key, start, end, as_of):
    facts = GoalCompletion.objects.filter(
        scope_key=scope_key, completed_at__gte=start, completed_at__lt=end,
        completed_at__lte=as_of,
    )
    # A single grouped query serves every goal; no joins that multiply facts.
    return list(facts.values('category_id_snapshot', 'group_id_snapshot').annotate(
        minutes=Sum('duration_minutes'), sessions=Count('id'),
    ).order_by())


def pending_reconciliations(scope_key, start, end, as_of):
    return Schedule.objects.filter(
        scope_key=scope_key, state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
        expected_end_at__gte=start, expected_end_at__lt=end, expected_end_at__lte=as_of,
    ).count()


def skip_totals(scope_key, start, end, as_of):
    facts = GoalActivitySkip.objects.filter(
        scope_key=scope_key, skipped_at__gte=start, skipped_at__lt=end,
        skipped_at__lte=as_of,
    )
    return list(facts.values(
        'category_id_snapshot', 'group_id_snapshot', 'activity_id_snapshot',
        'activity_name_snapshot',
    ).annotate(skip_count=Count('id')).order_by())
