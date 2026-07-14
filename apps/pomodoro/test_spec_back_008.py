import hashlib
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from apps.pomodoro.models import (
    Activity,
    ActivityQueue,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
)
from apps.pomodoro.serializers import ActivityExecutionSerializer
from apps.pomodoro.services.activity_queue import group_daily_metrics


class QueueContractTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-008')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.all_group, _ = Group.objects.get_or_create(name='Todos')
        self.all_group.is_default = True
        self.all_group.max_daily_minutes = 0
        self.all_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group = Group.objects.create(name='Jogos', max_daily_minutes=120)
        self.category = Category.objects.create(
            name='RPG',
            group=self.group,
            max_daily_executions=20,
        )

    def create_activity(self, name='Atividade', *, duration=30, category=None, active=True):
        return Activity.objects.create(
            name=name,
            duration=duration,
            category=category or self.category,
            active=active,
        )

    def next(self, group=None):
        selected = group or self.group
        return self.client.get(f'/api/activities/next/?group_id={selected.id}')

    def start(self, item_response):
        return self.client.post(
            f"/api/activities/{item_response.data['id']}/start/",
            {'queue_item_id': item_response.data['queue_item_id']},
            format='json',
        )

    def create_history(self, activity, *, completed=True):
        now = timezone.now()
        schedule = Schedule.objects.create(
            activity=activity,
            scheduled_date=timezone.localdate(),
            start_time=timezone.localtime(now).time().replace(tzinfo=None),
            state=Schedule.STATE_COMPLETED if completed else Schedule.STATE_RUNNING,
            completed=completed,
            starts_at=now,
            expected_end_at=now + timedelta(minutes=activity.duration),
            completed_at=now if completed else None,
        )
        return History.objects.create(
            activity=activity,
            schedule=schedule,
            start_time=now,
            end_time=now if completed else None,
            duration=activity.duration if completed else None,
        )

    def assert_queue_context(self, payload, *, group, consumed, remaining):
        self.assertEqual(payload['queue_group_id'], group.id)
        self.assertEqual(payload['queue_group_name'], group.name)
        self.assertEqual(payload['group_max_daily_minutes'], group.max_daily_minutes)
        self.assertEqual(payload['group_consumed_daily_minutes'], consumed)
        self.assertEqual(payload['group_remaining_daily_minutes'], remaining)

    def test_presented_item_exposes_position_queue_context_and_daily_balance(self):
        self.create_activity('Primeira')
        self.create_activity('Segunda')

        response = self.next()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['position'], 1)
        self.assertEqual(response.data['pool_size'], 2)
        self.assertEqual(response.data['consumed_count'], 0)
        self.assertEqual(response.data['queue_mode'], ActivityQueue.MODE_NORMAL)
        self.assertFalse(response.data['skip_locked'])
        self.assertIsNone(response.data['source_queue_id'])
        self.assert_queue_context(response.data, group=self.group, consumed=0, remaining=120)

    def test_all_uses_default_queue_group_and_unlimited_balance(self):
        self.create_activity()

        response = self.next(self.all_group)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assert_queue_context(response.data, group=self.all_group, consumed=0, remaining=None)
        self.assertEqual(response.data['group_id'], self.group.id)

    def test_review_exposes_locked_mode_and_source_queue(self):
        self.create_activity()
        normal = self.next()
        self.client.post(f"/api/activity-queue/items/{normal.data['queue_item_id']}/skip/")

        review = self.next()

        self.assertEqual(review.data['queue_mode'], ActivityQueue.MODE_SKIPPED_REVIEW)
        self.assertTrue(review.data['skip_locked'])
        self.assertEqual(review.data['source_queue_id'], normal.data['queue_id'])

    def test_execution_endpoints_return_identical_queue_context(self):
        self.create_activity(duration=30)
        item = self.next()
        started = self.start(item)
        schedule_id = started.data['schedule_id']

        active = self.client.get('/api/activities/active/')
        status_response = self.client.get(f'/api/activities/status/{schedule_id}/')
        retrieve = self.client.get(f'/api/activity-executions/{schedule_id}/')
        reconcile = self.client.post(f'/api/activity-executions/{schedule_id}/reconcile/')

        expected = {
            'queue_group_id': self.group.id,
            'queue_group_name': self.group.name,
            'queue_mode': ActivityQueue.MODE_NORMAL,
            'skip_locked': False,
            'group_max_daily_minutes': 120,
            'group_consumed_daily_minutes': 30,
            'group_remaining_daily_minutes': 90,
        }
        for response in [started, active, status_response, retrieve, reconcile]:
            self.assertEqual(response.status_code, status.HTTP_200_OK if response is not started else status.HTTP_201_CREATED)
            for key, value in expected.items():
                self.assertEqual(response.data[key], value)

        completed = self.client.post(
            '/api/activities/complete/',
            {'schedule_id': schedule_id},
            format='json',
        )
        for key, value in expected.items():
            self.assertEqual(completed.data[key], value)

    def test_temporal_contract_keeps_utc_instants_and_local_schedule_time(self):
        activity = self.create_activity(duration=25)
        item = self.next()
        fixed_now = datetime(2026, 7, 14, 0, 55, 27, tzinfo=ZoneInfo('UTC'))

        with patch('django.utils.timezone.now', return_value=fixed_now):
            started = self.start(item)
            active = self.client.get('/api/activities/active/')
            status_response = self.client.get(
                f"/api/activities/status/{started.data['schedule_id']}/"
            )
            retrieve = self.client.get(
                f"/api/activity-executions/{started.data['schedule_id']}/"
            )

        schedule = Schedule.objects.get(pk=started.data['schedule_id'])

        self.assertEqual(schedule.scheduled_date.isoformat(), '2026-07-13')
        self.assertEqual(schedule.start_time.strftime('%H:%M:%S'), '21:55:27')
        self.assertEqual(schedule.starts_at, fixed_now)
        self.assertIsNotNone(schedule.starts_at.tzinfo)
        self.assertEqual(schedule.expected_end_at, fixed_now + timedelta(minutes=25))

        self.assertEqual(started.data['start_time'], '21:55:27')
        for response in [started, active, status_response, retrieve]:
            self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])
            self.assertTrue(response.data['starts_at'].endswith('Z'))
            self.assertTrue(response.data['expected_end_at'].endswith('Z'))
            self.assertIsNotNone(response.data['server_now'].tzinfo)
            self.assertEqual(response.data['server_now'].utcoffset(), timedelta(0))
            self.assertEqual(
                datetime.fromisoformat(response.data['starts_at'].replace('Z', '+00:00')),
                fixed_now,
            )

    def test_active_execution_conflict_contains_canonical_context(self):
        first = self.create_activity('Primeira')
        item = self.next()
        self.start(item)
        other_group = Group.objects.create(name='Estudos')
        other_category = Category.objects.create(
            name='Cursos', group=other_group, max_daily_executions=10
        )
        second = self.create_activity('Segunda', category=other_category)
        other_item = self.next(other_group)

        conflict = self.client.post(
            f'/api/activities/{second.id}/start/',
            {'queue_item_id': other_item.data['queue_item_id']},
            format='json',
        )

        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflict.data['code'], 'active_execution_conflict')
        self.assertEqual(conflict.data['active_execution']['activity']['id'], first.id)
        self.assertEqual(conflict.data['active_execution']['queue_group_id'], self.group.id)

    def test_legacy_execution_without_queue_item_serializes_null_context(self):
        activity = self.create_activity()
        now = timezone.now()
        schedule = Schedule.objects.create(
            activity=activity,
            scheduled_date=timezone.localdate(),
            start_time=timezone.localtime(now).time().replace(tzinfo=None),
            state=Schedule.STATE_RUNNING,
            starts_at=now,
            expected_end_at=now + timedelta(minutes=30),
        )

        data = ActivityExecutionSerializer(schedule).data

        for field in [
            'queue_id',
            'queue_item_id',
            'queue_group_id',
            'queue_group_name',
            'queue_mode',
            'skip_locked',
            'group_max_daily_minutes',
            'group_consumed_daily_minutes',
            'group_remaining_daily_minutes',
        ]:
            self.assertIsNone(data[field])

    def test_execution_context_calculates_daily_metrics_once(self):
        self.create_activity()
        item = self.next()
        started = self.start(item)
        schedule = Schedule.objects.select_related(
            'activity__category__group',
            'queue_item__queue__group',
        ).get(pk=started.data['schedule_id'])

        with patch(
            'apps.pomodoro.serializers.group_daily_metrics',
            wraps=group_daily_metrics,
        ) as metrics:
            data = ActivityExecutionSerializer(schedule).data

        self.assertEqual(data['queue_group_id'], self.group.id)
        metrics.assert_called_once()

    def test_empty_queue_without_activities_has_structured_reason(self):
        response = self.next()

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['code'], 'no_activity_available')
        self.assertEqual(response.data['reason'], 'no_activities')
        self.assert_queue_context(response.data, group=self.group, consumed=0, remaining=120)

    def test_empty_queue_distinguishes_group_limit_and_activity_that_does_not_fit(self):
        consumed = self.create_activity('Consumida', duration=120)
        self.create_history(consumed)
        exhausted = self.next()
        self.assertEqual(exhausted.data['reason'], 'group_daily_time_limit_reached')

        self.group.max_daily_minutes = 150
        self.group.save(update_fields=['max_daily_minutes'])
        self.create_activity('Longa', duration=45)
        does_not_fit = self.next()
        self.assertEqual(does_not_fit.data['reason'], 'no_activity_fits_remaining_time')
        self.assertEqual(does_not_fit.data['group_remaining_daily_minutes'], 30)

    def test_empty_queue_distinguishes_category_limit_and_ambiguous_cause(self):
        limited_category = Category.objects.create(
            name='Limitada', group=self.group, max_daily_executions=1
        )
        consumed = self.create_activity('Consumida', category=limited_category)
        self.create_activity('Outra', category=limited_category)
        self.create_history(consumed)
        limited = self.next()
        self.assertEqual(limited.data['reason'], 'category_daily_limit_reached')

        limited_category.max_daily_executions = 10
        limited_category.save(update_fields=['max_daily_executions'])
        Activity.objects.exclude(pk=consumed.pk).update(active=False)
        ambiguous = self.next()
        self.assertEqual(ambiguous.data['reason'], 'unknown')

    def test_obsolete_item_codes_and_skip_idempotency(self):
        activity = self.create_activity()
        response = self.next()
        item = ActivityQueueItem.objects.get(pk=response.data['queue_item_id'])

        item.state = ActivityQueueItem.STATE_EXPIRED
        item.save(update_fields=['state'])
        expired_start = self.start(response)
        expired_skip = self.client.post(f'/api/activity-queue/items/{item.id}/skip/')
        self.assertEqual(expired_start.data['code'], 'queue_item_expired')
        self.assertTrue(expired_start.data['recoverable'])
        self.assertEqual(expired_skip.data['code'], 'queue_item_expired')

        item.state = ActivityQueueItem.STATE_COMPLETED
        item.save(update_fields=['state'])
        consumed_start = self.start(response)
        consumed_skip = self.client.post(f'/api/activity-queue/items/{item.id}/skip/')
        self.assertEqual(consumed_start.data['code'], 'queue_item_consumed')
        self.assertEqual(consumed_skip.data['code'], 'queue_item_consumed')

        item.state = ActivityQueueItem.STATE_PRESENTED
        item.save(update_fields=['state'])
        activity.active = False
        activity.save(update_fields=['active'])
        inactive = self.start(response)
        self.assertEqual(inactive.data['code'], 'activity_no_longer_eligible')

        activity.active = True
        activity.save(update_fields=['active'])
        first_skip = self.client.post(f'/api/activity-queue/items/{item.id}/skip/')
        skipped_start = self.start(response)
        repeated_skip = self.client.post(f'/api/activity-queue/items/{item.id}/skip/')
        self.assertEqual(first_skip.status_code, status.HTTP_200_OK)
        self.assertEqual(skipped_start.data['code'], 'queue_item_consumed')
        self.assertEqual(repeated_skip.status_code, status.HTTP_200_OK)

        item.state = ActivityQueueItem.STATE_PENDING
        item.save(update_fields=['state'])
        item.queue.state = ActivityQueue.STATE_CANCELLED
        item.queue.closed_at = timezone.now()
        item.queue.save(update_fields=['state', 'closed_at'])
        reconciled = self.start(response)
        self.assertEqual(reconciled.data['code'], 'queue_reconciled')

    def test_daily_limit_errors_include_operational_context(self):
        self.category.max_daily_executions = 1
        self.category.save(update_fields=['max_daily_executions'])
        consumed = self.create_activity('Consumida')
        self.create_history(consumed)
        pending = self.create_activity('Pendente')
        scope_key = hashlib.sha256(f'Api-Key {self.api_key}'.encode('utf-8')).hexdigest()
        queue = ActivityQueue.objects.create(scope_key=scope_key, group=self.group, pool_size=1)
        item = ActivityQueueItem.objects.create(
            queue=queue,
            activity=pending,
            position=1,
            state=ActivityQueueItem.STATE_PRESENTED,
        )
        response = self.client.post(
            f'/api/activities/{pending.id}/start/',
            {'queue_item_id': item.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'daily_limit_reached')
        self.assertEqual(response.data['category_id'], self.category.id)
        self.assertEqual(response.data['category_name'], self.category.name)
        self.assertEqual(response.data['max_daily_executions'], 1)
        self.assertEqual(response.data['started_daily_executions'], 1)

        queue.state = ActivityQueue.STATE_CANCELLED
        queue.closed_at = timezone.now()
        queue.save(update_fields=['state', 'closed_at'])
        other_category = Category.objects.create(
            name='Sem limite de quantidade',
            group=self.group,
            max_daily_executions=10,
        )
        long_activity = self.create_activity(
            'Nao cabe',
            duration=100,
            category=other_category,
        )
        group_queue = ActivityQueue.objects.create(
            scope_key=scope_key,
            group=self.group,
            pool_size=1,
        )
        group_item = ActivityQueueItem.objects.create(
            queue=group_queue,
            activity=long_activity,
            position=1,
            state=ActivityQueueItem.STATE_PRESENTED,
        )
        group_response = self.client.post(
            f'/api/activities/{long_activity.id}/start/',
            {'queue_item_id': group_item.id},
            format='json',
        )
        self.assertEqual(group_response.data['code'], 'group_daily_minutes_reached')
        self.assertEqual(group_response.data['queue_group_id'], self.group.id)
        self.assertEqual(group_response.data['group_consumed_daily_minutes'], 30)
        self.assertEqual(group_response.data['group_remaining_daily_minutes'], 90)
        self.assertEqual(group_response.data['activity_duration'], 100)
