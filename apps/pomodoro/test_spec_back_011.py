import random
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from apps.pomodoro.models import (
    Activity,
    ActivityPreferenceEvent,
    ActivityQueue,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
)
from apps.pomodoro.services.activity_queue import (
    recreate_active_queue,
    skip_item,
)


class QueueRecreationApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-011')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.default_group, _ = Group.objects.get_or_create(name='Todos')
        self.default_group.is_default = True
        self.default_group.max_daily_minutes = 0
        self.default_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group = Group.objects.create(name='SPEC 011', max_daily_minutes=0)
        self.other_group = Group.objects.create(name='SPEC 011 Outro', max_daily_minutes=0)
        self.category = Category.objects.create(
            name='Categoria SPEC 011',
            group=self.group,
            max_daily_executions=100,
        )
        self.other_category = Category.objects.create(
            name='Categoria SPEC 011 Outro',
            group=self.other_group,
            max_daily_executions=100,
        )

    def create_activity(self, name, *, category=None, active=True, premium=False):
        premium_fields = {}
        if premium:
            premium_fields = {
                'premium': True,
                'premium_from': timezone.localdate(),
                'premium_until': timezone.localdate(),
            }
        return Activity.objects.create(
            name=name,
            category=category or self.category,
            active=active,
            **premium_fields,
        )

    def next(self, group=None):
        selected = group or self.group
        return self.client.get(f'/api/activities/next/?group_id={selected.id}')

    def recreate(self, queue_id, group=None):
        selected = group or self.group
        return self.client.post(
            '/api/activity-queue/recreate/',
            {'group_id': selected.id, 'expected_queue_id': queue_id},
            format='json',
        )

    def test_recreate_cancels_expected_queue_and_preserves_group_isolation(self):
        first = self.create_activity('Primeira')
        second = self.create_activity('Segunda')
        other = self.create_activity('Outro grupo', category=self.other_category)
        current = self.next()
        other_current = self.next(self.other_group)
        old_queue = ActivityQueue.objects.get(pk=current.data['queue_id'])
        old_item = old_queue.items.get(activity_id=current.data['id'])

        response = self.recreate(old_queue.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_queue = ActivityQueue.objects.get(pk=response.data['queue_id'])
        old_queue.refresh_from_db()
        old_item.refresh_from_db()
        self.assertEqual(old_queue.state, ActivityQueue.STATE_CANCELLED)
        self.assertIsNotNone(old_queue.closed_at)
        self.assertEqual(old_item.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertEqual(new_queue.recreated_from_id, old_queue.id)
        self.assertEqual(new_queue.pool_number, old_queue.pool_number + 1)
        self.assertSetEqual(
            set(new_queue.items.values_list('activity_id', flat=True)),
            {first.id, second.id},
        )
        self.assertEqual(
            ActivityQueue.objects.filter(
                scope_key=new_queue.scope_key,
                group=self.group,
                state=ActivityQueue.STATE_ACTIVE,
            ).count(),
            1,
        )
        self.assertTrue(
            ActivityQueue.objects.filter(
                pk=other_current.data['queue_id'],
                state=ActivityQueue.STATE_ACTIVE,
            ).exists()
        )
        self.assertFalse(new_queue.items.filter(activity=other).exists())

    def test_skipped_activity_and_event_are_preserved_and_requeued_once(self):
        skipped = self.create_activity('Pulada')
        self.create_activity('Pendente')
        current = self.next()
        old_queue = ActivityQueue.objects.get(pk=current.data['queue_id'])
        old_item = old_queue.items.get(activity=skipped)
        if old_item.id != current.data['queue_item_id']:
            old_item = old_queue.items.get(pk=current.data['queue_item_id'])
            skipped = old_item.activity
        skip_item(queue_item_id=old_item.id, scope_key=old_queue.scope_key)
        event = ActivityPreferenceEvent.objects.get(
            queue_item=old_item,
            event_type=ActivityPreferenceEvent.EVENT_SKIPPED,
        )

        response = self.recreate(old_queue.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['requeued_skipped_count'], 1)
        new_queue = ActivityQueue.objects.get(pk=response.data['queue_id'])
        self.assertEqual(new_queue.items.filter(activity=skipped).count(), 1)
        old_item.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(old_item.state, ActivityQueueItem.STATE_SKIPPED)
        self.assertEqual(event.queue_id, old_queue.id)
        self.assertEqual(
            ActivityPreferenceEvent.objects.filter(
                activity=skipped,
                event_type=ActivityPreferenceEvent.EVENT_SKIPPED,
            ).count(),
            1,
        )

    def test_ineligible_skipped_activity_is_reported_without_blocking_recreation(self):
        self.create_activity('Pulada inativa')
        self.create_activity('Ainda elegivel')
        current = self.next()
        old_queue = ActivityQueue.objects.get(pk=current.data['queue_id'])
        skipped_item = old_queue.items.get(pk=current.data['queue_item_id'])
        remaining = old_queue.items.exclude(pk=skipped_item.pk).get().activity
        skip_item(queue_item_id=skipped_item.id, scope_key=old_queue.scope_key)
        skipped_item.activity.active = False
        skipped_item.activity.save(update_fields=['active'])

        response = self.recreate(old_queue.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['requeued_skipped_count'], 0)
        self.assertEqual(
            response.data['skipped_not_requeued'],
            [{'activity_id': skipped_item.activity_id, 'reason': 'inactive'}],
        )
        new_queue = ActivityQueue.objects.get(pk=response.data['queue_id'])
        self.assertSetEqual(
            set(new_queue.items.values_list('activity_id', flat=True)),
            {remaining.id},
        )

    def test_recreation_guards_expected_state_group_and_open_execution(self):
        self.create_activity('Protegida')
        current = self.next()
        queue_id = current.data['queue_id']

        missing = self.client.post(
            '/api/activity-queue/recreate/',
            {'group_id': self.group.id},
            format='json',
        )
        stale = self.recreate(queue_id + 999)
        missing_group = self.client.post(
            '/api/activity-queue/recreate/',
            {'group_id': 999999, 'expected_queue_id': queue_id},
            format='json',
        )

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing.data['code'], 'expected_queue_id_required')
        self.assertEqual(stale.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(stale.data['code'], 'queue_changed')
        self.assertEqual(missing_group.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(missing_group.data['code'], 'group_not_found')

        activity = Activity.objects.get(pk=current.data['id'])
        Schedule.objects.create(
            activity=activity,
            scope_key=ActivityQueue.objects.get(pk=queue_id).scope_key,
            state=Schedule.STATE_RUNNING,
            scheduled_date=timezone.localdate(),
            start_time=timezone.localtime().time().replace(tzinfo=None),
        )
        running = self.recreate(queue_id)
        self.assertEqual(running.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(running.data['code'], 'active_execution_running')

    def test_skipped_review_and_empty_candidate_keep_current_queue_active(self):
        activity = self.create_activity('Revisao')
        current = self.next()
        normal = ActivityQueue.objects.get(pk=current.data['queue_id'])
        normal.state = ActivityQueue.STATE_CLOSED
        normal.closed_at = timezone.now()
        normal.save(update_fields=['state', 'closed_at'])
        review = ActivityQueue.objects.create(
            scope_key=normal.scope_key,
            group=self.group,
            mode=ActivityQueue.MODE_SKIPPED_REVIEW,
            skip_locked=True,
            source_queue=normal,
        )
        ActivityQueueItem.objects.create(queue=review, activity=activity, position=1)

        locked = self.recreate(review.id)

        self.assertEqual(locked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(locked.data['code'], 'queue_recreation_locked')
        review.refresh_from_db()
        self.assertEqual(review.state, ActivityQueue.STATE_ACTIVE)

        review.state = ActivityQueue.STATE_CLOSED
        review.closed_at = timezone.now()
        review.save(update_fields=['state', 'closed_at'])
        empty_queue = ActivityQueue.objects.create(
            scope_key=normal.scope_key,
            group=self.group,
            mode=ActivityQueue.MODE_NORMAL,
        )
        ActivityQueueItem.objects.create(queue=empty_queue, activity=activity, position=1)
        activity.active = False
        activity.save(update_fields=['active'])

        empty = self.recreate(empty_queue.id)

        self.assertEqual(empty.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(empty.data['code'], 'no_activity_available')
        empty_queue.refresh_from_db()
        self.assertEqual(empty_queue.state, ActivityQueue.STATE_ACTIVE)

    def test_second_request_with_old_expected_id_returns_queue_changed(self):
        self.create_activity('Clique duplo')
        current = self.next()

        first = self.recreate(current.data['queue_id'])
        second = self.recreate(current.data['queue_id'])

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data['code'], 'queue_changed')
        self.assertEqual(second.data['queue_id'], first.data['queue_id'])

    def test_recreation_returns_not_found_when_group_has_no_active_queue(self):
        response = self.recreate(123456)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['code'], 'active_queue_not_found')


class QueueRecreationServiceTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='Servico SPEC 011')
        self.category = Category.objects.create(
            name='Categoria Servico SPEC 011',
            group=self.group,
            max_daily_executions=100,
        )
        self.activities = [
            Activity.objects.create(name=f'Atividade {index}', category=self.category)
            for index in range(4)
        ]
        self.queue = ActivityQueue.objects.create(
            scope_key='service-scope',
            group=self.group,
            pool_size=len(self.activities),
        )
        ActivityQueueItem.objects.bulk_create([
            ActivityQueueItem(queue=self.queue, activity=activity, position=position)
            for position, activity in enumerate(self.activities, start=1)
        ])

    def test_injected_rng_produces_deterministic_order(self):
        result = recreate_active_queue(
            scope_key=self.queue.scope_key,
            selected_group=self.group,
            expected_queue_id=self.queue.id,
            rng=random.Random(42),
        )

        expected_scores = []
        expected_rng = random.Random(42)
        for activity in self.activities:
            expected_scores.append((expected_rng.random(), activity.id))
        expected_ids = [
            activity_id
            for _score, activity_id in sorted(expected_scores, key=lambda row: (-row[0], row[1]))
        ]
        actual_ids = list(
            result.queue.items.order_by('position').values_list('activity_id', flat=True)
        )
        self.assertEqual(actual_ids, expected_ids)

    def test_recreation_keeps_active_premium_before_normal_activities(self):
        premium = self.activities[-1]
        premium.premium = True
        premium.premium_from = timezone.localdate()
        premium.premium_until = timezone.localdate()
        premium.save(update_fields=['premium', 'premium_from', 'premium_until'])

        result = recreate_active_queue(
            scope_key=self.queue.scope_key,
            selected_group=self.group,
            expected_queue_id=self.queue.id,
            rng=random.Random(1),
        )

        self.assertEqual(
            result.queue.items.order_by('position').first().activity_id,
            premium.id,
        )

    def test_failure_creating_new_queue_rolls_back_expiration_and_cancellation(self):
        with patch(
            'apps.pomodoro.services.activity_queue.ActivityQueue.objects.create',
            side_effect=RuntimeError('falha simulada'),
        ), self.assertRaises(RuntimeError):
            recreate_active_queue(
                scope_key=self.queue.scope_key,
                selected_group=self.group,
                expected_queue_id=self.queue.id,
                rng=random.Random(1),
            )

        self.queue.refresh_from_db()
        self.assertEqual(self.queue.state, ActivityQueue.STATE_ACTIVE)
        self.assertFalse(
            self.queue.items.filter(state=ActivityQueueItem.STATE_EXPIRED).exists()
        )


class QueueActivityListingApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-011-list')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.default_group, _ = Group.objects.get_or_create(name='Todos')
        self.default_group.is_default = True
        self.default_group.max_daily_minutes = 0
        self.default_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group = Group.objects.create(name='Lista SPEC 011', max_daily_minutes=0)
        self.category = Category.objects.create(
            name='Categoria Lista SPEC 011',
            group=self.group,
            max_daily_executions=100,
        )

    def create_activity(self, name):
        return Activity.objects.create(name=name, category=self.category)

    def next(self):
        return self.client.get(f'/api/activities/next/?group_id={self.group.id}')

    def create_history(self, activity, *, completed):
        now = timezone.now()
        schedule = Schedule.objects.create(
            activity=activity,
            scope_key=f'history-{activity.id}-{Schedule.objects.count()}',
            state=Schedule.STATE_COMPLETED if completed else Schedule.STATE_RUNNING,
            scheduled_date=timezone.localdate(),
            start_time=timezone.localtime(now).time().replace(tzinfo=None),
            completed_at=now if completed else None,
        )
        return History.objects.create(
            activity=activity,
            schedule=schedule,
            start_time=now,
            end_time=now if completed else None,
        )

    def test_listing_does_not_create_or_present_a_queue(self):
        response = self.client.get(
            f'/api/activity-queue/activities/?group_id={self.group.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['queue'])
        self.assertEqual(response.data['activities'], [])
        self.assertFalse(
            ActivityQueue.objects.filter(group=self.group, state=ActivityQueue.STATE_ACTIVE).exists()
        )

    def test_listing_returns_first_thirty_operational_items_without_mutation(self):
        for index in range(35):
            self.create_activity(f'Lista {index:02d}')
        current = self.next()
        queue = ActivityQueue.objects.get(pk=current.data['queue_id'])
        before = list(queue.items.values_list('id', 'position', 'state'))

        response = self.client.get(
            f'/api/activity-queue/activities/?group_id={self.group.id}&limit=100'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['returned_count'], 30)
        self.assertEqual(response.data['available_count'], 35)
        self.assertTrue(response.data['has_more'])
        self.assertEqual(
            [item['position'] for item in response.data['activities']],
            list(range(1, 31)),
        )
        self.assertEqual(before, list(queue.items.values_list('id', 'position', 'state')))

    def test_listing_ignores_consumed_items_and_aggregates_history_without_join_multiplication(self):
        activity = self.create_activity('Com estatisticas')
        never_completed = self.create_activity('Sem conclusao')
        ignored = self.create_activity('Ignorada')
        current = self.next()
        queue = ActivityQueue.objects.get(pk=current.data['queue_id'])
        ignored_item = queue.items.get(activity=ignored)
        ignored_item.state = ActivityQueueItem.STATE_SKIPPED
        ignored_item.skipped_at = timezone.now()
        ignored_item.save(update_fields=['state', 'skipped_at'])
        self.create_history(activity, completed=True)
        self.create_history(activity, completed=True)
        self.create_history(activity, completed=False)
        self.create_history(never_completed, completed=False)
        item = queue.items.get(activity=activity)
        for event_type in [
            ActivityPreferenceEvent.EVENT_SKIPPED,
            ActivityPreferenceEvent.EVENT_SKIPPED,
            ActivityPreferenceEvent.EVENT_SKIPPED_COMPLETED,
        ]:
            ActivityPreferenceEvent.objects.create(
                activity=activity,
                queue=queue,
                queue_item=item,
                event_type=event_type,
            )

        response = self.client.get(
            f'/api/activity-queue/activities/?group_id={self.group.id}'
        )

        by_id = {row['activity_id']: row for row in response.data['activities']}
        self.assertNotIn(ignored.id, by_id)
        self.assertEqual(by_id[activity.id]['execution_count'], 2)
        self.assertIsNotNone(by_id[activity.id]['last_execution_at'])
        self.assertEqual(by_id[activity.id]['skip_count'], 2)
        self.assertEqual(by_id[never_completed.id]['execution_count'], 0)
        self.assertIsNone(by_id[never_completed.id]['last_execution_at'])
