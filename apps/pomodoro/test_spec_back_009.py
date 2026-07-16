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

from apps.pomodoro.models import Activity, ActivityQueue, ActivityQueueItem, Category, Group
from apps.pomodoro.services.activity_queue import present_next_item
from apps.pomodoro.services.activity_queue_reconciliation import (
    activity_snapshot,
    reconcile_all_premium_queues,
    reconcile_premium_queue,
)


class PremiumQueueReconciliationTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='Grupo da fila', max_daily_minutes=300)
        self.other_group = Group.objects.create(name='Outro grupo', max_daily_minutes=300)
        self.category = Category.objects.create(
            name='Categoria da fila',
            group=self.group,
            max_daily_executions=20,
        )
        self.other_category = Category.objects.create(
            name='Categoria externa',
            group=self.other_group,
            max_daily_executions=20,
        )
        self.queue = ActivityQueue.objects.create(
            scope_key='spec-back-009',
            group=self.group,
        )
        self.today = timezone.localdate()

    def activity(self, name, *, premium=False, category=None, **kwargs):
        premium_fields = {}
        if premium:
            premium_fields = {
                'premium': True,
                'premium_from': self.today,
                'premium_until': self.today + timedelta(days=2),
            }
        return Activity.objects.create(
            name=name,
            category=category or self.category,
            **premium_fields,
            **kwargs,
        )

    def item(self, activity, position, state=ActivityQueueItem.STATE_PENDING):
        return ActivityQueueItem.objects.create(
            queue=self.queue,
            activity=activity,
            position=position,
            state=state,
        )

    def pending_names(self):
        return list(
            self.queue.items.filter(state=ActivityQueueItem.STATE_PENDING)
            .order_by('position')
            .values_list('activity__name', flat=True)
        )

    def test_promotes_and_inserts_global_premiums_without_moving_current_item(self):
        current = self.item(
            self.activity('Apresentada'),
            1,
            ActivityQueueItem.STATE_PRESENTED,
        )
        old_premium = self.activity('Premium anterior', premium=True)
        normal_one = self.activity('Normal 1')
        new_premium = self.activity('Premium nova', premium=True)
        normal_two = self.activity('Normal 2')
        self.item(old_premium, 2)
        self.item(normal_one, 3)
        self.item(new_premium, 4)
        self.item(normal_two, 5)
        missing_global = self.activity(
            'Premium global',
            premium=True,
            category=self.other_category,
        )
        self.queue.pool_size = 5
        self.queue.save(update_fields=['pool_size'])

        with transaction.atomic():
            result = reconcile_premium_queue(self.queue, rng=random.Random(4))

        current.refresh_from_db()
        self.queue.refresh_from_db()
        names = self.pending_names()
        self.assertEqual(current.position, 1)
        self.assertEqual(current.state, ActivityQueueItem.STATE_PRESENTED)
        self.assertEqual(set(names[:2]), {'Premium nova', 'Premium global'})
        self.assertEqual(names[2:], ['Premium anterior', 'Normal 1', 'Normal 2'])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.promoted, 1)
        self.assertEqual(self.queue.pool_size, 6)
        self.assertEqual(self.queue.consumed_count, 0)
        self.assertEqual(self.queue.items.filter(activity=missing_global).count(), 1)
        self.assertEqual(
            self.queue.items.count(),
            self.queue.items.values('position').distinct().count(),
        )

        positions = list(self.queue.items.order_by('position').values_list('position', flat=True))
        with transaction.atomic():
            repeated = reconcile_premium_queue(self.queue, rng=random.Random(99))
        self.assertFalse(repeated.changed)
        self.assertEqual(
            list(self.queue.items.order_by('position').values_list('position', flat=True)),
            positions,
        )

    def test_expired_premium_is_demoted_behind_active_premium(self):
        expired = self.activity('Premium expirada')
        expired.premium = True
        expired.premium_from = self.today - timedelta(days=3)
        expired.premium_until = self.today - timedelta(days=1)
        expired.save(update_fields=['premium', 'premium_from', 'premium_until'])
        active = self.activity('Premium vigente', premium=True)
        normal = self.activity('Normal')
        self.item(expired, 1)
        self.item(active, 2)
        self.item(normal, 3)

        with transaction.atomic():
            result = reconcile_premium_queue(self.queue, rng=random.Random(1))

        self.assertEqual(self.pending_names(), ['Premium vigente', 'Premium expirada', 'Normal'])
        self.assertEqual(result.demoted, 1)

    def test_consumed_item_is_not_resurrected_or_duplicated(self):
        premium = self.activity('Consumida premium', premium=True)
        consumed = self.item(premium, 1, ActivityQueueItem.STATE_COMPLETED)
        normal = self.item(self.activity('Normal'), 2)

        with transaction.atomic():
            reconcile_premium_queue(self.queue, rng=random.Random(1))

        consumed.refresh_from_db()
        normal.refresh_from_db()
        self.assertEqual(consumed.position, 1)
        self.assertEqual(consumed.state, ActivityQueueItem.STATE_COMPLETED)
        self.assertEqual(normal.position, 2)
        self.assertEqual(self.queue.items.filter(activity=premium).count(), 1)

    def test_dry_run_reports_changes_without_persisting_them(self):
        normal = self.activity('Normal')
        premium = self.activity('Premium ausente', premium=True, category=self.other_category)
        self.item(normal, 1)
        self.queue.pool_size = 1
        self.queue.save(update_fields=['pool_size'])

        summary = reconcile_all_premium_queues(rng=random.Random(1), dry_run=True)

        self.assertEqual(summary.queues_checked, 1)
        self.assertEqual(summary.items_inserted, 1)
        self.assertFalse(self.queue.items.filter(activity=premium).exists())
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.pool_size, 1)

    def test_review_and_closed_queues_are_not_reconciled(self):
        premium = self.activity('Premium', premium=True)
        review = ActivityQueue.objects.create(
            scope_key='review',
            group=self.group,
            mode=ActivityQueue.MODE_SKIPPED_REVIEW,
        )
        closed = ActivityQueue.objects.create(
            scope_key='closed',
            group=self.group,
            state=ActivityQueue.STATE_CLOSED,
        )

        summary = reconcile_all_premium_queues(rng=random.Random(1))

        self.assertEqual(summary.queues_checked, 1)
        self.assertFalse(review.items.filter(activity=premium).exists())
        self.assertFalse(closed.items.filter(activity=premium).exists())

    def test_snapshot_contains_premium_domain_state(self):
        premium = self.activity('Premium', premium=True)

        snapshot = activity_snapshot(premium)

        self.assertEqual(snapshot['premium_from'], self.today)
        self.assertEqual(snapshot['premium_until'], self.today + timedelta(days=2))
        self.assertTrue(snapshot['is_premium_active'])

    def test_queue_read_reconciles_before_presenting_next_pending_item(self):
        normal = self.item(self.activity('Normal'), 1)
        premium = self.item(self.activity('Premium', premium=True), 2)
        self.queue.pool_size = 2
        self.queue.save(update_fields=['pool_size'])

        result = present_next_item(scope_key=self.queue.scope_key, selected_group=self.group)

        normal.refresh_from_db()
        premium.refresh_from_db()
        self.assertEqual(result.item.id, premium.id)
        self.assertEqual(premium.state, ActivityQueueItem.STATE_PRESENTED)
        self.assertEqual(normal.state, ActivityQueueItem.STATE_PENDING)

    def test_new_group_queue_includes_global_premium_before_local_normal(self):
        local = self.activity('Normal local')
        global_premium = self.activity(
            'Premium global',
            premium=True,
            category=self.other_category,
        )
        self.queue.delete()

        result = present_next_item(scope_key='nova-fila', selected_group=self.group)

        self.assertEqual(result.item.activity_id, global_premium.id)
        self.assertTrue(result.item.queue.items.filter(activity=local).exists())

    def test_management_command_emits_structured_dry_run_summary(self):
        self.item(self.activity('Normal'), 1)
        self.activity('Premium', premium=True, category=self.other_category)
        output = io.StringIO()

        call_command('reconcile_premium_queues', '--dry-run', stdout=output)

        payload = json.loads(output.getvalue())
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['queues_checked'], 1)
        self.assertEqual(payload['items_inserted'], 1)


class PremiumQueueApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-009')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.group = Group.objects.create(name='Grupo API')
        self.category = Category.objects.create(
            name='Categoria API',
            group=self.group,
            max_daily_executions=10,
        )
        self.queue = ActivityQueue.objects.create(scope_key='api', group=self.group, pool_size=2)
        self.normal = Activity.objects.create(name='Normal', category=self.category)
        self.promoted = Activity.objects.create(name='Promovida', category=self.category)
        ActivityQueueItem.objects.create(queue=self.queue, activity=self.normal, position=1)
        ActivityQueueItem.objects.create(queue=self.queue, activity=self.promoted, position=2)

    def test_patch_confirms_immediate_promotion(self):
        today = timezone.localdate()

        response = self.client.patch(
            f'/api/activities/{self.promoted.id}/',
            {
                'premium': True,
                'premium_from': today.isoformat(),
                'premium_until': (today + timedelta(days=2)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first = self.queue.items.filter(
            state=ActivityQueueItem.STATE_PENDING
        ).order_by('position').first()
        self.assertEqual(first.activity_id, self.promoted.id)

    def test_invalid_premium_interval_returns_400_without_changing_queue(self):
        before = list(self.queue.items.order_by('position').values_list('activity_id', flat=True))
        today = timezone.localdate()

        response = self.client.patch(
            f'/api/activities/{self.promoted.id}/',
            {
                'premium': True,
                'premium_from': (today + timedelta(days=2)).isoformat(),
                'premium_until': today.isoformat(),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.promoted.refresh_from_db()
        self.assertFalse(self.promoted.premium)
        self.assertEqual(
            list(self.queue.items.order_by('position').values_list('activity_id', flat=True)),
            before,
        )
