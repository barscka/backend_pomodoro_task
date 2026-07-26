import io
import json
import random
from datetime import timedelta

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
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
    Schedule,
)
from apps.pomodoro.services.activity_execution import (
    ActivityExecutionConflict,
    start_activity,
)
from apps.pomodoro.services.activity_queue import present_next_item
from apps.pomodoro.services.activity_queue_reconciliation import (
    reconcile_activity,
    reconcile_all_premium_queues,
    reconcile_premium_queue,
)


class GroupQueueIsolationTests(TestCase):
    def setUp(self):
        self.all_group, _ = Group.objects.get_or_create(name='Todos')
        self.all_group.is_default = True
        self.all_group.max_daily_minutes = 0
        self.all_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group_a = Group.objects.create(name='Grupo A', max_daily_minutes=300)
        self.group_b = Group.objects.create(name='Grupo B', max_daily_minutes=300)
        self.category_a = Category.objects.create(
            name='Categoria A',
            group=self.group_a,
            max_daily_executions=20,
        )
        self.category_b = Category.objects.create(
            name='Categoria B',
            group=self.group_b,
            max_daily_executions=20,
        )
        self.today = timezone.localdate()

    def activity(self, name, *, category=None, premium=False):
        premium_fields = {}
        if premium:
            premium_fields = {
                'premium': True,
                'premium_from': self.today,
                'premium_until': self.today + timedelta(days=2),
            }
        return Activity.objects.create(
            name=name,
            category=category or self.category_a,
            **premium_fields,
        )

    def queue(self, scope, group, *, mode=ActivityQueue.MODE_NORMAL):
        return ActivityQueue.objects.create(
            scope_key=scope,
            group=group,
            mode=mode,
        )

    def item(self, queue, activity, position, state=ActivityQueueItem.STATE_PENDING):
        return ActivityQueueItem.objects.create(
            queue=queue,
            activity=activity,
            position=position,
            state=state,
        )

    def test_new_specific_queue_excludes_foreign_premium_and_all_remains_aggregate(self):
        local_normal = self.activity('Normal A')
        local_premium = self.activity('Premium A', premium=True)
        foreign_premium = self.activity(
            'Premium B',
            category=self.category_b,
            premium=True,
        )

        group_result = present_next_item(
            scope_key='specific',
            selected_group=self.group_a,
        )
        all_result = present_next_item(
            scope_key='aggregate',
            selected_group=self.all_group,
        )

        group_queue = group_result.item.queue
        all_queue = all_result.item.queue
        self.assertEqual(group_result.item.activity_id, local_premium.id)
        self.assertSetEqual(
            set(group_queue.items.values_list('activity_id', flat=True)),
            {local_normal.id, local_premium.id},
        )
        self.assertSetEqual(
            set(all_queue.items.values_list('activity_id', flat=True)),
            {local_normal.id, local_premium.id, foreign_premium.id},
        )

    def test_reconcile_activity_routes_premium_only_to_origin_group_and_all(self):
        local_seed = self.activity('Seed A')
        self.activity('Seed A pendente')
        foreign_seed = self.activity('Seed B', category=self.category_b)
        self.activity('Seed B pendente', category=self.category_b)
        queue_a = present_next_item(
            scope_key='route',
            selected_group=self.group_a,
        ).item.queue
        queue_b = present_next_item(
            scope_key='route',
            selected_group=self.group_b,
        ).item.queue
        queue_all = present_next_item(
            scope_key='route',
            selected_group=self.all_group,
        ).item.queue
        premium = self.activity('Nova premium A', premium=True)

        reconcile_activity(premium)
        reconcile_activity(premium)

        self.assertTrue(queue_a.items.filter(activity=premium).exists())
        self.assertFalse(queue_b.items.filter(activity=premium).exists())
        self.assertTrue(queue_all.items.filter(activity=premium).exists())
        self.assertEqual(queue_a.items.filter(activity=premium).count(), 1)
        self.assertEqual(queue_all.items.filter(activity=premium).count(), 1)
        self.assertTrue(queue_a.items.filter(activity=local_seed).exists())
        self.assertTrue(queue_b.items.filter(activity=foreign_seed).exists())

    def test_job_expires_foreign_pending_and_presented_but_preserves_started_and_final(self):
        queue = self.queue('legacy', self.group_a)
        local = self.item(queue, self.activity('Local'), 1)
        foreign_pending = self.item(
            queue,
            self.activity('Externa pendente', category=self.category_b, premium=True),
            2,
        )
        foreign_presented = self.item(
            queue,
            self.activity('Externa apresentada', category=self.category_b, premium=True),
            3,
            ActivityQueueItem.STATE_PRESENTED,
        )
        foreign_started = self.item(
            queue,
            self.activity('Externa iniciada', category=self.category_b, premium=True),
            4,
            ActivityQueueItem.STATE_STARTED,
        )
        foreign_completed = self.item(
            queue,
            self.activity('Externa concluida', category=self.category_b, premium=True),
            5,
            ActivityQueueItem.STATE_COMPLETED,
        )
        foreign_running = self.item(
            queue,
            self.activity('Externa executando', category=self.category_b, premium=True),
            6,
            ActivityQueueItem.STATE_PRESENTED,
        )
        Schedule.objects.create(
            activity=foreign_running.activity,
            queue_item=foreign_running,
            scope_key='legacy-running',
            state=Schedule.STATE_RUNNING,
            scheduled_date=self.today,
            start_time=timezone.localtime().time().replace(tzinfo=None),
        )

        with transaction.atomic():
            result = reconcile_premium_queue(queue, rng=random.Random(1))

        foreign_pending.refresh_from_db()
        foreign_presented.refresh_from_db()
        foreign_started.refresh_from_db()
        foreign_completed.refresh_from_db()
        foreign_running.refresh_from_db()
        local.refresh_from_db()
        self.assertEqual(result.foreign_items_expired, 2)
        self.assertEqual(foreign_pending.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertEqual(foreign_presented.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertEqual(foreign_started.state, ActivityQueueItem.STATE_STARTED)
        self.assertEqual(foreign_completed.state, ActivityQueueItem.STATE_COMPLETED)
        self.assertEqual(foreign_running.state, ActivityQueueItem.STATE_PRESENTED)
        self.assertEqual(local.state, ActivityQueueItem.STATE_PENDING)

    def test_read_expires_legacy_foreign_presented_before_returning_local(self):
        queue = self.queue('read', self.group_a)
        foreign = self.item(
            queue,
            self.activity('Externa', category=self.category_b, premium=True),
            1,
            ActivityQueueItem.STATE_PRESENTED,
        )
        local = self.item(queue, self.activity('Local'), 2)

        result = present_next_item(
            scope_key=queue.scope_key,
            selected_group=self.group_a,
        )

        foreign.refresh_from_db()
        local.refresh_from_db()
        self.assertEqual(foreign.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertEqual(result.item.id, local.id)
        self.assertEqual(local.state, ActivityQueueItem.STATE_PRESENTED)

    def test_start_rejects_foreign_presented_item_that_bypassed_queue_read(self):
        foreign_activity = self.activity(
            'Externa',
            category=self.category_b,
            premium=True,
        )
        foreign = self.item(
            self.queue('start', self.group_a),
            foreign_activity,
            1,
            ActivityQueueItem.STATE_PRESENTED,
        )

        with self.assertRaises(ActivityExecutionConflict) as raised:
            start_activity(
                activity=foreign_activity,
                queue_item=foreign,
                scope_key='start',
            )

        foreign.refresh_from_db()
        self.assertEqual(raised.exception.code, 'activity_no_longer_eligible')
        self.assertEqual(foreign.state, ActivityQueueItem.STATE_PRESENTED)

    def test_active_review_is_sanitized_without_reordering_valid_item(self):
        review = self.queue(
            'review',
            self.group_a,
            mode=ActivityQueue.MODE_SKIPPED_REVIEW,
        )
        foreign = self.item(
            review,
            self.activity('Externa', category=self.category_b, premium=True),
            1,
        )
        local = self.item(review, self.activity('Local'), 2)

        summary = reconcile_all_premium_queues(rng=random.Random(1))

        foreign.refresh_from_db()
        local.refresh_from_db()
        self.assertEqual(summary.queues_checked, 1)
        self.assertEqual(summary.foreign_items_expired, 1)
        self.assertEqual(foreign.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertEqual(local.state, ActivityQueueItem.STATE_PENDING)
        self.assertEqual(local.position, 2)

        repeated = reconcile_all_premium_queues(rng=random.Random(2))
        self.assertEqual(repeated.foreign_items_expired, 0)

    def test_command_dry_run_reports_foreign_expiration_without_persisting(self):
        queue = self.queue('command', self.group_a)
        foreign = self.item(
            queue,
            self.activity('Externa', category=self.category_b, premium=True),
            1,
        )
        output = io.StringIO()

        call_command('reconcile_premium_queues', '--dry-run', stdout=output)

        payload = json.loads(output.getvalue())
        foreign.refresh_from_db()
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['foreign_items_expired'], 1)
        self.assertEqual(foreign.state, ActivityQueueItem.STATE_PENDING)


class GroupQueueIsolationApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-010')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.group_a = Group.objects.create(name='API Grupo A')
        group_b = Group.objects.create(name='API Grupo B')
        self.category_a = Category.objects.create(
            name='API Categoria A',
            group=self.group_a,
        )
        category_b = Category.objects.create(
            name='API Categoria B',
            group=group_b,
        )
        today = timezone.localdate()
        self.foreign_premium = Activity.objects.create(
            name='API Premium B',
            category=category_b,
            premium=True,
            premium_from=today,
            premium_until=today + timedelta(days=2),
        )

    def test_next_returns_404_when_only_another_group_has_premium(self):
        response = self.client.get(
            f'/api/activities/next/?group_id={self.group_a.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['code'], 'no_activity_available')
        self.assertEqual(response.data['queue_group_id'], self.group_a.id)
        self.assertFalse(
            ActivityQueueItem.objects.filter(activity=self.foreign_premium).exists()
        )

    def test_api_creation_routes_premium_to_origin_group_and_all_only(self):
        Activity.objects.create(name='API Seed A1', category=self.category_a)
        Activity.objects.create(name='API Seed A2', category=self.category_a)
        category_b = self.foreign_premium.category
        Activity.objects.create(name='API Seed B', category=category_b)

        response_a = self.client.get(
            f'/api/activities/next/?group_id={self.group_a.id}'
        )
        response_b = self.client.get(
            f'/api/activities/next/?group_id={category_b.group_id}'
        )
        response_all = self.client.get('/api/activities/next/')
        today = timezone.localdate()

        created = self.client.post(
            '/api/activities/',
            {
                'name': 'API Premium A',
                'duration': 15,
                'category': self.category_a.id,
                'active': True,
                'premium': True,
                'premium_from': today.isoformat(),
                'premium_until': (today + timedelta(days=2)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ActivityQueueItem.objects.filter(
            queue_id=response_a.data['queue_id'],
            activity_id=created.data['id'],
        ).exists())
        self.assertFalse(ActivityQueueItem.objects.filter(
            queue_id=response_b.data['queue_id'],
            activity_id=created.data['id'],
        ).exists())
        self.assertTrue(ActivityQueueItem.objects.filter(
            queue_id=response_all.data['queue_id'],
            activity_id=created.data['id'],
        ).exists())
