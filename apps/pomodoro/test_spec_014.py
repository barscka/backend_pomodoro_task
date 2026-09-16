from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import Activity, Category, GoalCompletion, Group, History, PremiumPeriod, Schedule
from .services.activity_queue import category_started_count, group_reserved_minutes
from .services.premium_periods import PremiumPeriodConflict, create_period


@override_settings(PREMIUM_DIRECT_START_ENABLED=True)
class Spec014Tests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.key = APIKey.objects.create_key(name='spec-014')
        cls.group = Group.objects.create(name='Jogos', max_daily_minutes=60)
        cls.category = Category.objects.create(name='Premium', group=cls.group, max_daily_executions=1)
        cls.activity = Activity.objects.create(name='Jogo', category=cls.category, duration=60)

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')

    def period(self, **kwargs):
        today = timezone.localdate()
        return PremiumPeriod.objects.create(activity=self.activity, kind='paid', title='Passe',
            starts_on=kwargs.get('starts_on', today), ends_on=kwargs.get('ends_on', today + timedelta(days=2)))

    def test_period_overlap_is_rejected_and_adjacent_renewal_is_allowed(self):
        today = timezone.localdate()
        self.period(starts_on=today, ends_on=today + timedelta(days=2))
        with self.assertRaises(PremiumPeriodConflict):
            create_period(activity_id=self.activity.id, kind='focus', title='Sobreposto', starts_on=today + timedelta(days=1), ends_on=today + timedelta(days=3))
        renewal = create_period(activity_id=self.activity.id, kind='focus', title='Renovação', starts_on=today + timedelta(days=3), ends_on=today + timedelta(days=4))
        self.assertEqual(renewal.starts_on, today + timedelta(days=3))

    def test_direct_start_replay_completion_and_queue_quotas(self):
        period = self.period()
        request_id = str(uuid4())
        payload = {'duration_minutes': 30, 'request_id': request_id, 'return_group_id': self.group.id}
        first = self.client.post(f'/api/premium-periods/{period.id}/start/', payload, format='json')
        replay = self.client.post(f'/api/premium-periods/{period.id}/start/', payload, format='json')
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.data['execution_id'], replay.data['execution_id'])
        self.assertIsNone(first.data['queue_item_id'])
        self.assertEqual(first.data['execution_origin'], 'premium_direct')
        self.assertEqual(first.data['planned_duration_seconds'], 1800)
        complete = self.client.post('/api/activities/complete/', {'schedule_id': first.data['execution_id']}, format='json')
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(GoalCompletion.objects.count(), 1)
        self.assertEqual(category_started_count(self.category), 0)
        self.assertEqual(group_reserved_minutes(self.group), 0)
        self.assertEqual(History.objects.count(), 1)

    def test_idempotency_payload_conflict_and_single_open_execution(self):
        period = self.period()
        key = str(uuid4())
        self.assertEqual(self.client.post(f'/api/premium-periods/{period.id}/start/', {'duration_minutes': 15, 'request_id': key}, format='json').status_code, 201)
        conflict = self.client.post(f'/api/premium-periods/{period.id}/start/', {'duration_minutes': 30, 'request_id': key}, format='json')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['code'], 'idempotency_payload_conflict')
        other = self.client.post(f'/api/premium-periods/{period.id}/start/', {'duration_minutes': 15, 'request_id': str(uuid4())}, format='json')
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.data['code'], 'active_execution_conflict')

    def test_three_direct_blocks_can_continue_without_queue_side_effects(self):
        period = self.period()
        predecessor = None
        for index in range(3):
            if predecessor is None:
                response = self.client.post(f'/api/premium-periods/{period.id}/start/',
                    {'duration_minutes': 15, 'request_id': str(uuid4())}, format='json')
            else:
                response = self.client.post(f'/api/activity-executions/{predecessor["execution_id"]}/continue/',
                    {'duration_minutes': 15, 'request_id': str(uuid4()), 'expected_version': predecessor['version']}, format='json')
            self.assertEqual(response.status_code, 201)
            completed = self.client.post('/api/activities/complete/', {'schedule_id': response.data['execution_id']}, format='json')
            self.assertEqual(completed.status_code, 200)
            predecessor = completed.data
        self.assertEqual(Schedule.objects.filter(execution_origin='premium_direct').count(), 3)
        self.assertEqual(GoalCompletion.objects.count(), 3)
        self.assertFalse(Schedule.objects.filter(queue_item__isnull=False).exists())

    def test_report_splits_at_midnight_and_period_end(self):
        day = timezone.localdate() - timedelta(days=2)
        period = self.period(starts_on=day, ends_on=day)
        zone = ZoneInfo('America/Sao_Paulo')
        start = datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=23, minutes=40)
        schedule = Schedule.objects.create(activity=self.activity, scheduled_date=day, start_time=start.time(), completed=True,
            scope_key='scope', state='completed', starts_at=start, completed_at=start + timedelta(hours=1), execution_origin='legacy')
        History.objects.create(activity=self.activity, schedule=schedule, start_time=start, end_time=start + timedelta(hours=1), duration=60)
        GoalCompletion.objects.create(source_schedule_id=schedule.id, scope_key='scope', category_id_snapshot=self.category.id,
            group_id_snapshot=self.group.id, completed_at=start + timedelta(hours=1), duration_minutes=60,
            activity_id_snapshot=self.activity.id, activity_name_snapshot=self.activity.name, started_at=start,
            duration_seconds=3600, execution_origin='legacy', context_source='execution_start')
        from .services.premium_reporting import period_stats
        self.assertEqual(period_stats(period, 'scope')['consolidated_seconds'], 1200)

    def test_settings_default_is_read_only_and_patch_is_versioned(self):
        response = self.client.get('/api/gameplay-tracking-settings/')
        self.assertEqual(response.data['daily_reference_minutes'], 300)
        self.assertEqual(response.data['version'], 0)
        patched = self.client.patch('/api/gameplay-tracking-settings/', {'daily_reference_minutes': 240, 'group_ids': [self.group.id], 'expected_version': 0}, format='json')
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data['daily_reference_minutes'], 240)
