import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as utc_timezone
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from apps.pomodoro.models import (
    Activity, ActivityQueue, ActivityQueueItem, Category, GoalCompletion, Group,
    History, Schedule, WeeklyGoal, WeeklyGoalRevision,
)
from apps.pomodoro.services.activity_execution import (
    ActivityExecutionConflict, complete_schedule, reconcile_schedule, start_activity,
)
from apps.pomodoro.services.goal_completions import record_completion
from apps.pomodoro.services.weekly_goals import GoalError, current_week, update_goal, week_bounds


NOW = datetime(2026, 9, 9, 15, tzinfo=utc_timezone.utc)
WEEK = date(2026, 9, 7)


class GoalFixtures:
    def setup_domain(self):
        self.group = Group.objects.create(name='Metas estudo')
        self.category = Category.objects.create(name='Metas leitura', group=self.group, max_daily_executions=100)
        self.other_group = Group.objects.create(name='Metas lazer')
        self.other_category = Category.objects.create(name='Metas jogos', group=self.other_group, max_daily_executions=100)
        self.activity = Activity.objects.create(name='Ler', category=self.category, duration=60)

    def schedule(self, *, scope=None, state=Schedule.STATE_RUNNING, start=None,
                 minutes=60, snapshot=True):
        start = start or NOW - timedelta(hours=1)
        completed = state == Schedule.STATE_COMPLETED
        end = start + timedelta(minutes=minutes)
        schedule = Schedule.objects.create(
            activity=self.activity, scope_key=scope if scope is not None else self.scope,
            scheduled_date=start.date(), start_time=start.time().replace(tzinfo=None),
            starts_at=start, expected_end_at=end, state=state, completed=completed,
            completed_at=end if completed else None,
            goal_category_id_snapshot=self.category.pk if snapshot else None,
            goal_group_id_snapshot=self.group.pk if snapshot else None,
        )
        History.objects.create(
            activity=self.activity, schedule=schedule, start_time=start,
            end_time=end if completed else None, duration=minutes if completed else None,
        )
        return schedule

    def fact(self, *, scope=None, category=None, group=None, when=None, minutes=60):
        return GoalCompletion.objects.create(
            source_schedule_id=100000 + GoalCompletion.objects.count(),
            scope_key=self.scope if scope is None else scope,
            category_id_snapshot=(category or self.category).pk,
            group_id_snapshot=(group or self.group).pk,
            completed_at=when or NOW - timedelta(minutes=1), duration_minutes=minutes,
            context_source='execution_start',
        )


class WeeklyGoalApiTests(GoalFixtures, APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.key = APIKey.objects.create_key(name='weekly-goals')
        _, cls.other_key = APIKey.objects.create_key(name='weekly-goals-other')

    def setUp(self):
        self.setup_domain()
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')
        self.scope = hashlib.sha256(f'Api-Key {self.key}'.encode()).hexdigest()
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def create_goal(self, **changes):
        payload = {'metric': 'minutes', 'group_id': self.group.pk, 'target': 240}
        if 'category_id' in changes:
            payload.pop('group_id')
        payload.update(changes)
        response = self.client.post('/api/weekly-goals/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def progress(self, **query):
        response = self.client.get('/api/weekly-goals/progress/', query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_create_progress_overlapping_metrics_and_retroactive_week(self):
        self.fact()
        self.fact()
        goal = self.create_goal()
        session_goal = self.create_goal(category_id=self.category.pk, metric='sessions', target=2)
        self.fact(scope='other-scope', minutes=900)
        self.fact(when=NOW - timedelta(days=7), minutes=900)
        response = self.progress()
        by_id = {row['goal_id']: row for row in response['results']}
        self.assertEqual(by_id[goal['id']]['achieved'], 120)
        self.assertEqual(by_id[goal['id']]['remaining'], 120)
        self.assertEqual(by_id[goal['id']]['progress_percent'], '50.00')
        self.assertFalse(by_id[goal['id']]['is_achieved'])
        self.assertEqual(by_id[session_goal['id']]['achieved'], 2)
        self.assertTrue(by_id[session_goal['id']]['is_achieved'])
        self.assertNotIn('scope_key', goal)
        self.assertEqual(goal['effective_week'], WEEK.isoformat())
        self.assertEqual(response['week_end_exclusive'], '2026-09-14')

    def test_zero_minutes_overachievement_and_rounding(self):
        self.fact(minutes=0)
        self.create_goal(metric='sessions', target=3)
        self.assertEqual(self.progress()['results'][0]['progress_percent'], '33.33')
        for _ in range(3):
            self.fact(minutes=0)
        row = self.progress()['results'][0]
        self.assertEqual(row['remaining'], 0)
        self.assertEqual(row['progress_percent'], '133.33')
        self.assertTrue(row['is_achieved'])

    def test_scope_isolation_and_authentication_for_all_actions(self):
        goal = self.create_goal()
        self.fact()
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.other_key}')
        self.assertEqual(self.progress()['count'], 0)
        self.assertEqual(self.client.get('/api/weekly-goals/').data['count'], 0)
        self.assertEqual(self.client.get(f"/api/weekly-goals/{goal['id']}/").status_code, 404)
        response = self.client.patch(f"/api/weekly-goals/{goal['id']}/", {
            'target': 400, 'expected_version': 1,
        }, format='json')
        self.assertEqual(response.status_code, 404)
        self.create_goal()
        self.assertEqual(self.progress()['results'][0]['achieved'], 0)
        self.client.credentials()
        for method, path, body in [
            ('get', '/api/weekly-goals/', None), ('get', '/api/weekly-goals/progress/', None),
            ('get', f"/api/weekly-goals/{goal['id']}/", None),
            ('post', '/api/weekly-goals/', {}),
            ('patch', f"/api/weekly-goals/{goal['id']}/", {'target': 1, 'expected_version': 1}),
        ]:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, body, format='json')
                self.assertIn(response.status_code, [401, 403])

    def test_validation_and_disallowed_methods(self):
        valid = {'metric': 'minutes', 'group_id': self.group.pk, 'target': 240}
        for changes in [
            {'scope_key': 'forged'}, {'version': 1}, {'active': False}, {'is_all_groups': True},
            {'target': 0}, {'target': -1}, {'target': 1.5}, {'target': 2147483648},
            {'group_id': 999999}, {'group_id': None}, {'metric': 'hours'},
            {'category_id': self.category.pk},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post('/api/weekly-goals/', {**valid, **changes}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'invalid_goal')
        response = self.client.post('/api/weekly-goals/', {'metric': 'minutes', 'target': 1}, format='json')
        self.assertEqual(response.status_code, 400)
        goal = self.create_goal()
        path = f"/api/weekly-goals/{goal['id']}/"
        for data in [{'target': 1}, {'expected_version': 1}, {'expected_version': 1, 'metric': 'sessions'}]:
            self.assertEqual(self.client.patch(path, data, format='json').status_code, 400)
        self.assertEqual(self.client.put(path, valid, format='json').status_code, 405)
        self.assertEqual(self.client.delete(path).status_code, 405)

    def test_duplicate_even_inactive_and_revision_version_conflict(self):
        goal = self.create_goal()
        path = f"/api/weekly-goals/{goal['id']}/"
        response = self.client.patch(path, {'active': False, 'expected_version': 1}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['version'], 2)
        duplicate = self.client.post('/api/weekly-goals/', {
            'metric': 'minutes', 'group_id': self.group.pk, 'target': 100,
        }, format='json')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.data['code'], 'goal_already_exists')
        stale = self.client.patch(path, {'target': 100, 'expected_version': 1}, format='json')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.progress()['count'], 0)
        self.assertEqual(self.client.get('/api/weekly-goals/?active=false').data['count'], 1)
        active = self.client.patch(path, {'active': True, 'expected_version': 2}, format='json')
        self.assertTrue(active.data['active'])
        self.assertEqual(WeeklyGoalRevision.objects.count(), 1)

    def test_revision_preserves_previous_week_and_no_goal_before_creation(self):
        last_week = NOW - timedelta(days=7)
        with patch('django.utils.timezone.now', return_value=last_week):
            goal = self.create_goal()
        path = f"/api/weekly-goals/{goal['id']}/"
        self.fact(when=last_week)
        response = self.client.patch(path, {'target': 120, 'expected_version': 1}, format='json')
        self.assertEqual(response.data['target'], 120)
        old = self.progress(week_start='2026-08-31')['results'][0]
        self.assertEqual(old['target'], 240)
        self.assertEqual(old['achieved'], 60)
        self.assertEqual(self.progress(week_start='2026-08-24')['count'], 0)
        self.client.patch(path, {'active': False, 'expected_version': 2}, format='json')
        self.assertEqual(self.progress()['count'], 0)
        self.assertEqual(self.progress(week_start='2026-08-31')['count'], 1)
        self.assertEqual(WeeklyGoalRevision.objects.count(), 2)

    def test_read_only_pending_reconciliation_and_local_week_boundary(self):
        self.create_goal()
        start, end = week_bounds(WEEK)
        self.fact(when=start - timedelta(microseconds=1), minutes=100)
        self.fact(when=start, minutes=10)
        self.fact(when=end, minutes=100)
        schedule = self.schedule(start=NOW - timedelta(hours=2))
        self.schedule(scope='other', start=NOW - timedelta(hours=2))
        with CaptureQueriesContext(connection) as queries:
            response = self.progress()
        self.assertEqual(response['results'][0]['achieved'], 10)
        self.assertEqual(response['pending_reconciliation_count'], 1)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        schedule.refresh_from_db()
        self.assertEqual(schedule.state, Schedule.STATE_RUNNING)
        self.assertEqual(self.client.post(f'/api/activity-executions/{schedule.pk}/reconcile/').status_code, 200)
        self.assertEqual(self.progress()['results'][0]['achieved'], 70)
        self.assertEqual(self.progress()['pending_reconciliation_count'], 0)

    def test_all_group_snapshot_and_category_filters(self):
        all_group = Group.objects.get(is_default=True)
        all_goal = self.create_goal(group_id=all_group.pk)
        specific = self.create_goal()
        category = self.create_goal(category_id=self.other_category.pk)
        self.fact(minutes=10)
        self.fact(group=self.other_group, category=self.other_category, minutes=20)
        # Changing the default group must not change the meaning of an existing goal.
        self.other_group.is_default = True
        self.other_group.save()
        rows = {row['goal_id']: row for row in self.progress()['results']}
        self.assertEqual(rows[all_goal['id']]['achieved'], 30)
        self.assertEqual(rows[specific['id']]['achieved'], 10)
        self.assertEqual(rows[category['id']]['achieved'], 20)

    def test_pagination_dates_and_constant_query_count(self):
        for index in range(25):
            category = Category.objects.create(name=f'Meta {index}', group=self.group)
            self.create_goal(category_id=category.pk)
        self.assertEqual(len(self.progress()['results']), 20)
        self.assertEqual(len(self.progress(page=2)['results']), 5)
        self.assertEqual(len(self.progress(page_size=999)['results']), 25)
        with CaptureQueriesContext(connection) as small:
            self.progress(page_size=1)
        with CaptureQueriesContext(connection) as large:
            self.progress(page_size=100)
        self.assertEqual(len(small), len(large))
        for week in ['2026-09-08', '2026-09-14', 'garbage', '2026-02-30', '20260907', '']:
            response = self.client.get('/api/weekly-goals/progress/', {'week_start': week})
            self.assertEqual(response.status_code, 400, week)
        self.assertEqual(self.client.get('/api/weekly-goals/?active=maybe').status_code, 400)


class GoalCompletionTests(GoalFixtures, TestCase):
    def setUp(self):
        self.setup_domain()
        self.scope = 'test-scope'
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_start_captures_origin_even_in_all_queue_and_review(self):
        all_group = Group.objects.get(is_default=True)
        queue = ActivityQueue.objects.create(scope_key=self.scope, group=all_group, mode='skipped_review')
        item = ActivityQueueItem.objects.create(queue=queue, activity=self.activity, position=1)
        schedule, created = start_activity(activity=self.activity, queue_item=item, scope_key=self.scope)
        self.assertTrue(created)
        self.assertEqual(schedule.goal_group_id_snapshot, self.group.pk)
        self.assertEqual(schedule.goal_category_id_snapshot, self.category.pk)
        with patch('django.utils.timezone.now', return_value=NOW + timedelta(hours=1)):
            complete_schedule(schedule)
        fact = GoalCompletion.objects.get()
        self.assertEqual(fact.context_source, 'execution_start')
        self.assertEqual(fact.group_id_snapshot, self.group.pk)

    def test_idempotency_reclassification_duration_change_and_deletion(self):
        schedule = self.schedule()
        self.activity.category = self.other_category
        self.activity.duration = 900
        self.activity.save(update_fields=['category', 'duration'])
        complete_schedule(schedule)
        complete_schedule(schedule)
        self.assertEqual(GoalCompletion.objects.count(), 1)
        fact = GoalCompletion.objects.get()
        self.assertEqual(fact.duration_minutes, 60)
        self.assertEqual(fact.category_id_snapshot, self.category.pk)
        self.activity.delete()
        self.assertFalse(Schedule.objects.filter(pk=schedule.pk).exists())
        self.assertEqual(GoalCompletion.objects.get().duration_minutes, 60)

    def test_fact_failure_rolls_back_schedule_history_and_queue(self):
        queue = ActivityQueue.objects.create(scope_key=self.scope, group=self.group, pool_size=1)
        item = ActivityQueueItem.objects.create(queue=queue, activity=self.activity, position=1)
        with patch('django.utils.timezone.now', return_value=NOW - timedelta(hours=1)):
            schedule, _ = start_activity(activity=self.activity, queue_item=item, scope_key=self.scope)
        with patch('apps.pomodoro.services.goal_completions.GoalCompletion.objects.get_or_create',
                   side_effect=RuntimeError('fact failure')), self.assertRaises(RuntimeError):
            complete_schedule(schedule)
        schedule.refresh_from_db()
        item.refresh_from_db()
        queue.refresh_from_db()
        self.assertEqual(schedule.state, Schedule.STATE_RUNNING)
        self.assertEqual(item.state, ActivityQueueItem.STATE_STARTED)
        self.assertEqual(queue.consumed_count, 0)
        self.assertIsNone(History.objects.get(schedule=schedule).end_time)

    def test_cancelled_and_expired_do_not_complete(self):
        for state in [Schedule.STATE_CANCELLED, Schedule.STATE_EXPIRED]:
            for completed_flag in [False, True]:
                schedule = self.schedule(state=state)
                Schedule.objects.filter(pk=schedule.pk).update(completed=completed_flag)
                with self.assertRaises(ActivityExecutionConflict):
                    complete_schedule(schedule)
        self.assertFalse(GoalCompletion.objects.exists())

    def test_late_reconciliation_uses_expected_completion_week(self):
        schedule = self.schedule(start=datetime(2026, 9, 7, 1, tzinfo=utc_timezone.utc))
        reconcile_schedule(schedule)
        fact = GoalCompletion.objects.get()
        self.assertEqual(current_week(fact.completed_at), date(2026, 8, 31))
        self.assertEqual(fact.duration_minutes, 60)

    def test_early_completion_zero_minutes_and_legacy_context(self):
        schedule = self.schedule(start=NOW - timedelta(seconds=15), snapshot=False)
        complete_schedule(schedule)
        fact = GoalCompletion.objects.get()
        self.assertEqual(fact.duration_minutes, 0)
        self.assertEqual(fact.completed_at, NOW)
        self.assertEqual(fact.context_source, 'legacy_current')

    def test_backfill_dry_run_idempotency_and_invalid_legacy(self):
        valid = self.schedule(state=Schedule.STATE_COMPLETED, snapshot=False)
        self.schedule(scope='', state=Schedule.STATE_COMPLETED)
        self.schedule(scope='anonymous', state=Schedule.STATE_COMPLETED)
        invalid = self.schedule(state=Schedule.STATE_COMPLETED)
        History.objects.filter(schedule=invalid).update(duration=None)
        self.schedule(state=Schedule.STATE_CANCELLED)
        counts_before = Activity.objects.get(pk=self.activity.pk).executions_today
        output = StringIO()
        call_command('backfill_goal_completions', dry_run=True, batch_size=2, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['eligible'], 1)
        self.assertEqual(report['excluded']['missing_scope'], 2)
        self.assertFalse(GoalCompletion.objects.exists())
        for _ in range(2):
            call_command('backfill_goal_completions', batch_size=2, stdout=StringIO())
        self.assertEqual(GoalCompletion.objects.count(), 1)
        fact = GoalCompletion.objects.get(source_schedule_id=valid.pk)
        self.assertEqual(fact.context_source, 'legacy_current')
        self.assertEqual(Activity.objects.get(pk=self.activity.pk).executions_today, counts_before)
        History.objects.filter(schedule=valid).update(duration=900)
        call_command('backfill_goal_completions', stdout=StringIO())
        fact.refresh_from_db()
        self.assertEqual(fact.duration_minutes, 60)

    def test_live_and_backfill_share_one_fact_and_completed_retry_repairs_missing(self):
        schedule = self.schedule(state=Schedule.STATE_COMPLETED)
        complete_schedule(schedule)
        call_command('backfill_goal_completions', stdout=StringIO())
        self.assertEqual(GoalCompletion.objects.count(), 1)
        self.assertEqual(record_completion(schedule), 'existing')

    def test_year_boundary_and_local_midnight(self):
        self.assertEqual(current_week(datetime(2027, 1, 1, tzinfo=utc_timezone.utc)), date(2026, 12, 28))
        self.assertEqual(current_week(datetime(2026, 9, 7, 2, 59, tzinfo=utc_timezone.utc)), date(2026, 8, 31))
        start, end = week_bounds(WEEK)
        self.assertEqual(start.hour, 3)
        self.assertEqual(end - start, timedelta(days=7))


class GoalConstraintTests(GoalFixtures, TestCase):
    def setUp(self):
        self.setup_domain()

    def test_destination_metric_and_revision_constraints(self):
        for fields in [
            {}, {'group': self.group, 'category': self.category},
            {'category': self.category, 'is_all_groups': True},
            {'group': self.group, 'metric': 'hours'},
        ]:
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                WeeklyGoal.objects.create(**{'scope_key': 'a', 'metric': 'minutes', **fields})
        goal = WeeklyGoal.objects.create(scope_key='a', metric='minutes', group=self.group)
        for target, week in [(0, WEEK), (1, WEEK + timedelta(days=1))]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                WeeklyGoalRevision.objects.create(goal=goal, effective_week=week, target=target)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeeklyGoal.objects.create(scope_key='a', metric='minutes', group=self.group)
        with self.assertRaises(ProtectedError):
            self.group.delete()


@skipUnlessDBFeature('has_select_for_update')
class GoalConcurrencyTests(GoalFixtures, TransactionTestCase):
    """Run explicitly against disposable PostgreSQL; SQLite cannot verify row locks."""

    def setUp(self):
        self.setup_domain()
        self.scope = 'concurrency-test'
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def run_parallel(self, first, second):
        barrier = Barrier(2)

        def worker(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker, operation) for operation in (first, second)]
            return [job.result(timeout=20) for job in jobs]

    def test_concurrent_edits_have_one_winner(self):
        goal = WeeklyGoal.objects.create(scope_key=self.scope, metric='minutes', group=self.group)
        WeeklyGoalRevision.objects.create(goal=goal, effective_week=WEEK, target=240)

        def edit(target):
            try:
                return update_goal(scope_key=self.scope, goal_id=goal.pk, week=WEEK,
                                   data={'target': target, 'expected_version': 1}).target
            except GoalError as exc:
                return exc.code

        results = self.run_parallel(lambda: edit(120), lambda: edit(180))
        self.assertEqual(results.count('goal_version_conflict'), 1)
        goal.refresh_from_db()
        self.assertEqual(goal.version, 2)
        self.assertIn(goal.revisions.get().target, [120, 180])

    def test_concurrent_completions_create_one_fact(self):
        schedule = self.schedule()
        self.run_parallel(lambda: complete_schedule(schedule), lambda: complete_schedule(schedule))
        self.assertEqual(GoalCompletion.objects.filter(source_schedule_id=schedule.pk).count(), 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.version, 2)

    def test_backfill_racing_live_completion_is_idempotent(self):
        schedule = self.schedule()
        self.run_parallel(lambda: complete_schedule(schedule),
                          lambda: call_command('backfill_goal_completions', stdout=StringIO()))
        self.assertEqual(GoalCompletion.objects.filter(source_schedule_id=schedule.pk).count(), 1)
