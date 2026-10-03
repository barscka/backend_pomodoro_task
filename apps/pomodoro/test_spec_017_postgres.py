from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .models import Activity, ActivityQueue, ActivityQueueItem, Category, Group, PremiumPeriod, RoutinePlan, RoutineRevision, Schedule
from .services import routines
from .services.activity_execution import ActivityExecutionConflict
from .services.routine_selection import preview, start_from_preview


@skipUnlessDBFeature('has_select_for_update')
@override_settings(PREMIUM_DIRECT_START_ENABLED=True)
class RoutinePostgresTests(TransactionTestCase):
    def setUp(self):
        self.scope = 'routine-postgres-test'
        self.group = Group.objects.create(name='Games PG')
        self.category = Category.objects.create(name='Gameplay PG', group=self.group, max_daily_executions=20)
        self.activity = Activity.objects.create(name='Jogo PG', category=self.category)
        self.day = timezone.now().astimezone(routines.ZONE).date()
        routines.save_revision(self.scope, {'expected_version': 0, 'effective_from': self.day}, template=True)
        self.row = next(r for r in routines.expand(self.scope, self.day, self.day)['occurrences'] if r['kind'] == 'gameplay' and r['origin_date'] == self.day.isoformat())
        self.now = self.row['starts_at'] + timedelta(minutes=5)
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def threaded(self, func):
        close_old_connections()
        try:
            return func()
        except (routines.RoutineError, ActivityExecutionConflict) as exc:
            return ('conflict', exc.code)
        finally:
            close_old_connections()

    def test_concurrent_revisions_have_one_winner(self):
        self.assertEqual(connection.vendor, 'postgresql')
        data = {'expected_version': 1, 'effective_from': self.day + timedelta(days=7), 'blocks': routines.initial_blocks()}
        def save():
            routines.save_revision(self.scope, data)
            return ('ok',)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.threaded(save), range(2)))
        self.assertEqual(results.count(('ok',)), 1)
        self.assertIn(('conflict', 'stale_routine_version'), results)
        self.assertEqual(RoutineRevision.objects.count(), 2)

    def test_concurrent_initial_template_is_idempotent(self):
        RoutinePlan.objects.all().delete()
        def apply():
            result = routines.save_revision(self.scope, {'expected_version': 0, 'effective_from': self.day}, template=True)
            return ('ok', result['version'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.threaded(apply), range(2)))
        self.assertEqual(results, [('ok', 1), ('ok', 1)])
        self.assertEqual(RoutineRevision.objects.count(), 1)

    def _select(self, source):
        data = {'expected_version': 1, 'origin_date': self.day, 'block_id': UUID(self.row['block_id']), 'source': source}
        if source == 'premium':
            period = PremiumPeriod.objects.create(activity=self.activity, kind='paid', title='Premium', starts_on=self.day, ends_on=self.day + timedelta(days=2))
            data['premium_period_id'] = period.id
        else:
            queue = ActivityQueue.objects.create(scope_key=self.scope, group=self.group)
            self.item = ActivityQueueItem.objects.create(queue=queue, activity=self.activity, position=1)
            data['group_id'] = self.group.id
        routines.save_selection(self.scope, data)
        value, _ = preview(self.scope, self.day, UUID(self.row['block_id']))
        return {'origin_date': self.day, 'block_id': UUID(self.row['block_id']), 'preview_token': value['preview_token']}

    def test_concurrent_premium_start_has_one_execution(self):
        body = self._select('premium')
        def start(request_id):
            schedule, created = start_from_preview(self.scope, {**body, 'request_id': request_id})
            return ('ok', schedule.id, created)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda uid: self.threaded(lambda: start(uid)), [uuid4(), uuid4()]))
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertEqual(sum(r[0] == 'ok' for r in results), 1)
        self.assertIn(('conflict', 'stale_preview'), results)

    def test_concurrent_queue_replay_has_one_execution(self):
        body = {**self._select('queue'), 'request_id': uuid4()}
        def start():
            schedule, created = start_from_preview(self.scope, body)
            return ('ok', schedule.id, created)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.threaded(start), range(2)))
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertEqual(sum(r[2] for r in results), 1)
        self.assertEqual(results[0][1], results[1][1])

    def test_skipped_item_before_start_never_starts_replacement(self):
        body = self._select('queue')
        self.item.state = 'skipped'
        self.item.save()
        other = Activity.objects.create(name='Outro PG', category=self.category)
        ActivityQueueItem.objects.create(queue=self.item.queue, activity=other, position=2)
        with self.assertRaises(routines.RoutineError) as error:
            start_from_preview(self.scope, {**body, 'request_id': uuid4()})
        self.assertEqual(error.exception.code, 'stale_preview')
        self.assertFalse(Schedule.objects.exists())

    def test_concurrent_selections_have_one_version_winner(self):
        data = {'expected_version': 1, 'origin_date': self.day, 'block_id': UUID(self.row['block_id']), 'source': 'queue', 'group_id': self.group.id}
        def save():
            routines.save_selection(self.scope, data)
            return ('ok',)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.threaded(save), range(2)))
        self.assertEqual(results.count(('ok',)), 1)
        self.assertIn(('conflict', 'stale_routine_version'), results)

    def test_item_changed_while_start_waits_for_lock_returns_conflict(self):
        from threading import Event
        from django.db import transaction
        body = {**self._select('queue'), 'request_id': uuid4()}
        other = Activity.objects.create(name='Próximo PG', category=self.category)
        next_item = ActivityQueueItem.objects.create(queue=self.item.queue, activity=other, position=2)
        ready = Event()
        def start():
            ready.set()
            start_from_preview(self.scope, body)
            return ('ok',)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                item = ActivityQueueItem.objects.select_for_update().get(pk=self.item.id)
                pending = pool.submit(self.threaded, start)
                self.assertTrue(ready.wait(timeout=5))
                item.state = 'skipped'
                item.save(update_fields=['state'])
            result = pending.result(timeout=10)
        self.assertEqual(result, ('conflict', 'stale_preview'))
        self.assertFalse(Schedule.objects.exists())
        next_item.refresh_from_db()
        self.assertEqual(next_item.state, 'pending')
