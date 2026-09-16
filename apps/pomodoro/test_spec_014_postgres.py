from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .models import Activity, Category, Group, PremiumPeriod, Schedule
from .services.activity_execution import ActivityExecutionConflict
from .services.premium_execution import start_premium


@skipUnlessDBFeature('has_select_for_update')
@override_settings(PREMIUM_DIRECT_START_ENABLED=True)
class PremiumPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        group = Group.objects.create(name='Jogos')
        category = Category.objects.create(name='Premium concorrente', group=group)
        activity = Activity.objects.create(name='Jogo concorrente', category=category)
        today = timezone.localdate()
        self.period = PremiumPeriod.objects.create(activity=activity, kind='focus', title='Foco', starts_on=today, ends_on=today + timedelta(days=1))

    def _start(self, request_id):
        close_old_connections()
        try:
            schedule, created = start_premium(period_id=self.period.id, scope_key='concurrent-scope', duration_minutes=30, request_id=request_id)
            return ('ok', schedule.id, created)
        except ActivityExecutionConflict as exc:
            return ('conflict', exc.code)
        finally:
            close_old_connections()

    def test_two_devices_create_only_one_open_execution(self):
        self.assertEqual(connection.vendor, 'postgresql')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self._start, [uuid4(), uuid4()]))
        self.assertEqual(Schedule.objects.filter(scope_key='concurrent-scope', state='running').count(), 1)
        self.assertEqual(sum(result[0] == 'ok' for result in results), 1)
        self.assertIn(('conflict', 'active_execution_conflict'), results)
