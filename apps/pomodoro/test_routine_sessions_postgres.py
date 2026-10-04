from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from . import test_spec_017_postgres as fixtures
from .models import PremiumPeriod, RoutineSessionAssociation, Schedule
from .services.premium_execution import start_premium


@skipUnlessDBFeature('has_select_for_update')
@override_settings(PREMIUM_DIRECT_START_ENABLED=True)
class RoutineSessionPostgresTests(TransactionTestCase):
    setUp = fixtures.RoutinePostgresTests.setUp
    threaded = fixtures.RoutinePostgresTests.threaded

    def test_concurrent_direct_starts_create_one_binding(self):
        period = PremiumPeriod.objects.create(activity=self.activity, kind='paid', title='Direto PG', starts_on=self.day, ends_on=self.day + timedelta(days=1))
        def start(uid):
            schedule, created = start_premium(period_id=period.id, scope_key=self.scope, duration_minutes=30, request_id=uid)
            return ('ok', schedule.id, created)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda uid: self.threaded(lambda: start(uid)), [uuid4(), uuid4()]))
        self.assertEqual(sum(r[0] == 'ok' for r in results), 1)
        self.assertIn(('conflict', 'active_execution_conflict'), results)
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertEqual(RoutineSessionAssociation.objects.count(), 1)

    def test_concurrent_direct_retries_keep_original_occurrence(self):
        period = PremiumPeriod.objects.create(activity=self.activity, kind='paid', title='Replay PG', starts_on=self.day, ends_on=self.day + timedelta(days=1))
        request_id = uuid4()
        def start():
            schedule, created = start_premium(period_id=period.id, scope_key=self.scope, duration_minutes=30, request_id=request_id)
            return ('ok', schedule.id, created)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.threaded(start), range(2)))
        self.assertEqual(sum(r[2] for r in results), 1)
        self.assertEqual(results[0][1], results[1][1])
        association = RoutineSessionAssociation.objects.get()
        self.assertEqual(str(association.block_id), self.row['block_id'])
        self.assertEqual(association.session_starts_at, self.now)
