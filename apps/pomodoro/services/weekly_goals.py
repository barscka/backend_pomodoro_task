from datetime import date, datetime, time, timedelta, timezone as utc_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction

from apps.pomodoro.models import WeeklyGoal, WeeklyGoalRevision
from apps.pomodoro.repositories.weekly_goals import completion_totals, goals_for_week, skip_totals


GOAL_TIMEZONE = ZoneInfo('America/Sao_Paulo')


class GoalError(Exception):
    def __init__(self, code, detail, status=400):
        self.code, self.detail, self.status = code, detail, status
        super().__init__(detail)


def current_week(now):
    today = now.astimezone(GOAL_TIMEZONE).date()
    return today - timedelta(days=today.weekday())


def parse_week(value, now):
    week = current_week(now)
    if value is None:
        return week
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed.weekday() != 0 or parsed > week:
            raise ValueError
    except (TypeError, ValueError):
        raise GoalError('invalid_week', 'Informe uma segunda-feira ISO até a semana atual.')
    return parsed


def week_bounds(week):
    end_week = week + timedelta(days=7)
    start = datetime.combine(week, time.min, GOAL_TIMEZONE).astimezone(utc_timezone.utc)
    end = datetime.combine(end_week, time.min, GOAL_TIMEZONE).astimezone(utc_timezone.utc)
    return start, end


def create_goal(*, scope_key, data, week):
    try:
        with transaction.atomic():
            group = data.get('group')
            goal = WeeklyGoal.objects.create(
                scope_key=scope_key, metric=data['metric'], group=group,
                category=data.get('category'), is_all_groups=bool(group and group.is_default),
            )
            WeeklyGoalRevision.objects.create(goal=goal, effective_week=week, target=data['target'])
    except IntegrityError:
        # Translate only the expected uniqueness conflict, not unrelated DB failures.
        if WeeklyGoal.objects.filter(
            scope_key=scope_key, metric=data['metric'], group=data.get('group'),
            category=data.get('category'),
        ).exists():
            raise GoalError('goal_already_exists', 'Já existe uma meta para este destino e métrica.', 409)
        raise
    return goals_for_week(scope_key, week).get(pk=goal.pk)


@transaction.atomic
def update_goal(*, scope_key, goal_id, data, week):
    goal = WeeklyGoal.objects.select_for_update().filter(pk=goal_id, scope_key=scope_key).first()
    if goal is None:
        raise GoalError('goal_not_found', 'Meta não encontrada.', 404)
    if goal.version != data['expected_version']:
        raise GoalError('goal_version_conflict', 'A meta foi alterada; recarregue antes de editar.', 409)
    previous = goal.revisions.filter(effective_week__lte=week).order_by('-effective_week').first()
    if previous is None:
        raise GoalError('invalid_goal', 'A meta ainda não possui revisão vigente.')
    WeeklyGoalRevision.objects.update_or_create(
        goal=goal, effective_week=week,
        defaults={'target': data.get('target', previous.target), 'active': data.get('active', previous.active)},
    )
    goal.version += 1
    goal.save(update_fields=['version', 'updated_at'])
    return goals_for_week(scope_key, week).get(pk=goal.pk)


def _destination(goal):
    if goal.group_id:
        return {
            'type': 'group', 'id': goal.group_id, 'name': goal.group.name,
            'color': goal.group.color, 'group_id': goal.group_id,
            'group_name': goal.group.name,
        }
    return {
        'type': 'category', 'id': goal.category_id, 'name': goal.category.name,
        'color': goal.category.color, 'group_id': goal.category.group_id,
        'group_name': goal.category.group.name,
    }


def _skip_indexes(rows):
    categories, groups, overall = {}, {}, {}

    def merge(index, destination_id, activity_id, value):
        current = index.setdefault(destination_id, {}).setdefault(
            activity_id, {**value, 'skip_count': 0},
        )
        current['skip_count'] += value['skip_count']

    for row in rows:
        activity_id = row['activity_id_snapshot']
        value = {
            'activity_id': activity_id,
            'name': row['activity_name_snapshot'],
            'skip_count': row['skip_count'],
        }
        merge(categories, row['category_id_snapshot'], activity_id, value)
        merge(groups, row['group_id_snapshot'], activity_id, value)
        current = overall.setdefault(activity_id, {**value, 'skip_count': 0})
        current['skip_count'] += row['skip_count']
    return categories, groups, overall


def _activity_signals(values, include_most_skipped):
    activities = list(values.values())
    result = {
        'skip_count': sum(row['skip_count'] for row in activities),
        'distinct_activities_skipped': len(activities),
    }
    if include_most_skipped:
        result['most_skipped'] = sorted(
            activities, key=lambda row: (-row['skip_count'], row['name'], row['activity_id']),
        )[:3]
    return result


def progress_rows(goals, *, scope_key, start, end, as_of, include_activity_signals=False):
    totals = completion_totals(scope_key, start, end, as_of)
    skip_categories, skip_groups, skip_overall = _skip_indexes(
        skip_totals(scope_key, start, end, as_of),
    )
    categories, groups = {}, {}
    overall = {'minutes': 0, 'sessions': 0}
    for row in totals:
        category = categories.setdefault(row['category_id_snapshot'], {'minutes': 0, 'sessions': 0})
        group = groups.setdefault(row['group_id_snapshot'], {'minutes': 0, 'sessions': 0})
        for metric in overall:
            category[metric] += row[metric]
            group[metric] += row[metric]
            overall[metric] += row[metric]
    result = []
    for goal in goals:
        values = (overall if goal.is_all_groups else groups.get(goal.group_id, {})
                  if goal.group_id else categories.get(goal.category_id, {}))
        skips = (skip_overall if goal.is_all_groups else skip_groups.get(goal.group_id, {})
                 if goal.group_id else skip_categories.get(goal.category_id, {}))
        achieved = values.get(goal.metric, 0)
        percent = (Decimal(achieved) * 100 / Decimal(goal.target)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
        result.append({
            'goal_id': goal.pk, 'metric': goal.metric,
            'group_id': goal.group_id, 'category_id': goal.category_id,
            'destination': _destination(goal),
            'target': goal.target, 'achieved': achieved,
            'remaining': max(goal.target - achieved, 0), 'progress_percent': str(percent),
            'is_achieved': achieved >= goal.target,
            'activity_signals': _activity_signals(skips, include_activity_signals),
        })
    return result
