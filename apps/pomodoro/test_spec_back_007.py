from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
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
from apps.pomodoro.services.activity_execution import (
    ActivityExecutionConflict,
    complete_schedule,
    start_activity,
)
from apps.pomodoro.services.activity_queue import present_next_item, skip_item
from apps.pomodoro.services.activity_queue_reconciliation import (
    activity_snapshot,
    reconcile_activity,
)


class QueueByGroupApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='spec-back-007')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.all_group, _ = Group.objects.get_or_create(name='Todos')
        self.all_group.is_default = True
        self.all_group.max_daily_minutes = 0
        self.all_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group_a = Group.objects.create(name='Grupo A')
        self.group_b = Group.objects.create(name='Grupo B')
        self.category_a = Category.objects.create(
            name='Categoria A', group=self.group_a, max_daily_executions=100
        )
        self.category_b = Category.objects.create(
            name='Categoria B', group=self.group_b, max_daily_executions=100
        )

    def create_activity(self, name, category=None, duration=25, active=True):
        return Activity.objects.create(
            name=name,
            category=category or self.category_a,
            duration=duration,
            active=active,
        )

    def next(self, group=None):
        suffix = f'?group_id={group.id}' if group else ''
        return self.client.get(f'/api/activities/next/{suffix}')

    def start(self, response):
        return self.client.post(
            f"/api/activities/{response.data['id']}/start/",
            {'queue_item_id': response.data['queue_item_id']},
            format='json',
        )

    def complete(self, response):
        return self.client.post(
            '/api/activities/complete/',
            {'schedule_id': response.data['schedule_id']},
            format='json',
        )

    def test_group_a_group_b_and_all_keep_three_independent_active_queues(self):
        activity_a = self.create_activity('A')
        activity_b = self.create_activity('B', self.category_b)

        response_a = self.next(self.group_a)
        response_b = self.next(self.group_b)
        response_all = self.next()

        self.assertEqual(response_a.data['id'], activity_a.id)
        self.assertEqual(response_b.data['id'], activity_b.id)
        self.assertIn(response_all.data['id'], [activity_a.id, activity_b.id])
        self.assertEqual(len({response_a.data['queue_id'], response_b.data['queue_id'], response_all.data['queue_id']}), 3)
        self.assertEqual(ActivityQueue.objects.filter(state=ActivityQueue.STATE_ACTIVE).count(), 3)
        self.assertEqual(response_all.data['queue_group_id'], self.all_group.id)

    def test_switching_groups_preserves_presented_items(self):
        self.create_activity('A1')
        self.create_activity('A2')
        self.create_activity('B1', self.category_b)

        first_a = self.next(self.group_a)
        self.next(self.group_b)
        second_a = self.next(self.group_a)

        self.assertEqual(first_a.data['queue_item_id'], second_a.data['queue_item_id'])

    def test_active_queue_is_unique_per_scope_and_group(self):
        self.create_activity('A')
        first = self.next(self.group_a)
        queue = ActivityQueue.objects.get(pk=first.data['queue_id'])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActivityQueue.objects.create(scope_key=queue.scope_key, group=self.group_a)

    def test_normal_queue_contains_more_than_thirty_unique_activities_and_positions(self):
        for index in range(35):
            self.create_activity(f'Atividade {index}')

        response = self.next(self.group_a)
        queue = ActivityQueue.objects.get(pk=response.data['queue_id'])
        activity_ids = list(queue.items.values_list('activity_id', flat=True))
        positions = list(queue.items.values_list('position', flat=True))

        self.assertEqual(queue.pool_size, 35)
        self.assertEqual(len(activity_ids), len(set(activity_ids)))
        self.assertEqual(len(positions), len(set(positions)))
        self.assertEqual(sorted(positions), list(range(1, 36)))

    def test_skip_is_idempotent_and_creates_immediate_locked_review(self):
        activity = self.create_activity('Pulada')
        normal_response = self.next(self.group_a)
        skip_url = f"/api/activity-queue/items/{normal_response.data['queue_item_id']}/skip/"

        first_skip = self.client.post(skip_url)
        second_skip = self.client.post(skip_url)
        review = ActivityQueue.objects.get(source_queue_id=normal_response.data['queue_id'])
        review_response = self.next(self.group_a)

        self.assertEqual(first_skip.status_code, status.HTTP_200_OK)
        self.assertEqual(second_skip.status_code, status.HTTP_200_OK)
        self.assertEqual(review.mode, ActivityQueue.MODE_SKIPPED_REVIEW)
        self.assertTrue(review.skip_locked)
        self.assertEqual(list(review.items.values_list('activity_id', flat=True)), [activity.id])
        self.assertEqual(
            ActivityPreferenceEvent.objects.filter(event_type=ActivityPreferenceEvent.EVENT_SKIPPED).count(),
            1,
        )
        blocked = self.client.post(
            f"/api/activity-queue/items/{review_response.data['queue_item_id']}/skip/"
        )
        self.assertEqual(blocked.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(blocked.data['code'], 'skip_locked')

    def test_review_completion_records_event_and_allows_new_normal_cycle(self):
        self.create_activity('Pulada')
        normal = self.next(self.group_a)
        self.client.post(f"/api/activity-queue/items/{normal.data['queue_item_id']}/skip/")
        review_item = self.next(self.group_a)
        started = self.start(review_item)
        self.complete(started)

        review = ActivityQueue.objects.get(pk=review_item.data['queue_id'])
        review.refresh_from_db()
        self.assertEqual(review.state, ActivityQueue.STATE_CLOSED)
        self.assertTrue(ActivityPreferenceEvent.objects.filter(
            queue=review,
            event_type=ActivityPreferenceEvent.EVENT_SKIPPED_COMPLETED,
        ).exists())

        new_activity = self.create_activity('Novo ciclo')
        next_cycle = self.next(self.group_a)
        self.assertEqual(next_cycle.data['id'], new_activity.id)
        self.assertEqual(next_cycle.data['queue_mode'], ActivityQueue.MODE_NORMAL)

    def test_completed_normal_without_skip_does_not_create_review(self):
        self.create_activity('Concluida')
        item = self.next(self.group_a)
        started = self.start(item)
        self.complete(started)

        queue = ActivityQueue.objects.get(pk=item.data['queue_id'])
        self.assertEqual(queue.state, ActivityQueue.STATE_CLOSED)
        self.assertFalse(ActivityQueue.objects.filter(source_queue=queue).exists())

    def test_skips_are_isolated_between_specific_groups_and_all(self):
        activity_a = self.create_activity('A')
        activity_b = self.create_activity('B', self.category_b)
        item_a = self.next(self.group_a)
        item_b = self.next(self.group_b)
        item_all = self.next()

        self.client.post(f"/api/activity-queue/items/{item_a.data['queue_item_id']}/skip/")
        self.client.post(f"/api/activity-queue/items/{item_all.data['queue_item_id']}/skip/")
        remaining_all = self.next()
        self.client.post(f"/api/activity-queue/items/{remaining_all.data['queue_item_id']}/skip/")

        review_a = ActivityQueue.objects.get(source_queue_id=item_a.data['queue_id'])
        self.assertEqual(list(review_a.items.values_list('activity_id', flat=True)), [activity_a.id])
        self.assertFalse(ActivityQueue.objects.filter(source_queue_id=item_b.data['queue_id']).exists())
        review_all = ActivityQueue.objects.get(source_queue_id=item_all.data['queue_id'])
        self.assertEqual(review_all.group, self.all_group)
        self.assertEqual(
            set(review_all.items.values_list('activity_id', flat=True)),
            {activity_a.id, activity_b.id},
        )

    def test_only_one_open_execution_is_allowed_across_groups(self):
        self.create_activity('A')
        self.create_activity('B', self.category_b)
        item_a = self.next(self.group_a)
        item_b = self.next(self.group_b)

        self.assertEqual(self.start(item_a).status_code, status.HTTP_201_CREATED)
        conflict = self.start(item_b)

        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflict.data['code'], 'active_execution_conflict')

    def test_activity_created_by_api_is_reconciled_into_group_and_all_queues(self):
        self.create_activity('Existente')
        queue_a_id = self.next(self.group_a).data['queue_id']
        queue_all_id = self.next().data['queue_id']

        response = self.client.post(
            '/api/activities/',
            {
                'name': 'Criada pela API',
                'duration': 15,
                'category': self.category_a.id,
                'active': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ActivityQueueItem.objects.filter(
            queue_id=queue_a_id,
            activity_id=response.data['id'],
        ).exists())
        self.assertTrue(ActivityQueueItem.objects.filter(
            queue_id=queue_all_id,
            activity_id=response.data['id'],
        ).exists())


class QueueReconciliationTests(TestCase):
    def setUp(self):
        self.all_group, _ = Group.objects.get_or_create(name='Todos')
        self.all_group.is_default = True
        self.all_group.max_daily_minutes = 0
        self.all_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group_a = Group.objects.create(name='A')
        self.group_b = Group.objects.create(name='B')
        self.category_a = Category.objects.create(name='CA', group=self.group_a, max_daily_executions=100)
        self.category_b = Category.objects.create(name='CB', group=self.group_b, max_daily_executions=100)
        self.activity_a = Activity.objects.create(name='A1', category=self.category_a)
        self.activity_a_two = Activity.objects.create(name='A2', category=self.category_a)
        self.queue_a = present_next_item(scope_key='scope', selected_group=self.group_a).queue
        self.queue_all = present_next_item(scope_key='scope', selected_group=self.all_group).queue

    def test_new_and_activated_activity_enters_eligible_normal_queues_once(self):
        new_activity = Activity.objects.create(name='Nova', category=self.category_a)
        reconcile_activity(new_activity)
        reconcile_activity(new_activity)
        inactive = Activity.objects.create(name='Inativa', category=self.category_a, active=False)
        previous = activity_snapshot(inactive)
        inactive.active = True
        inactive.save(update_fields=['active'])
        reconcile_activity(inactive, previous=previous)

        for queue in [self.queue_a, self.queue_all]:
            self.assertEqual(queue.items.filter(activity=new_activity).count(), 1)
            self.assertEqual(queue.items.filter(activity=inactive).count(), 1)

    def test_deactivation_and_group_change_expire_old_items_and_add_new_eligible_items(self):
        item_a = self.queue_a.items.get(activity=self.activity_a)
        item_all = self.queue_all.items.get(activity=self.activity_a)
        previous = activity_snapshot(self.activity_a)
        self.activity_a.category = self.category_b
        self.activity_a.save(update_fields=['category'])
        queue_b = present_next_item(scope_key='scope', selected_group=self.group_b).queue
        reconcile_activity(self.activity_a, previous=previous)

        item_a.refresh_from_db()
        item_all.refresh_from_db()
        self.assertEqual(item_a.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertNotEqual(item_all.state, ActivityQueueItem.STATE_EXPIRED)
        self.assertTrue(queue_b.items.filter(activity=self.activity_a).exists())

        previous = activity_snapshot(self.activity_a)
        self.activity_a.active = False
        self.activity_a.save(update_fields=['active'])
        reconcile_activity(self.activity_a, previous=previous)
        item_all.refresh_from_db()
        self.assertEqual(item_all.state, ActivityQueueItem.STATE_EXPIRED)

    def test_started_item_and_expected_end_are_preserved(self):
        item = self.queue_a.items.get(activity=self.activity_a)
        schedule, _ = start_activity(activity=self.activity_a, queue_item=item, scope_key='scope')
        expected_end = schedule.expected_end_at
        previous = activity_snapshot(self.activity_a)
        self.activity_a.active = False
        self.activity_a.duration = 120
        self.activity_a.save(update_fields=['active', 'duration'])
        reconcile_activity(self.activity_a, previous=previous)

        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.state, ActivityQueueItem.STATE_STARTED)
        self.assertEqual(schedule.expected_end_at, expected_end)

    def test_reconciliation_preserves_consumed_positions_and_existing_relative_order(self):
        first = self.queue_a.items.get(activity=self.activity_a)
        skip_item(queue_item_id=first.id, scope_key='scope')
        consumed_position = first.position
        extra_one = Activity.objects.create(name='Extra 1', category=self.category_a)
        extra_two = Activity.objects.create(name='Extra 2', category=self.category_a)
        reconcile_activity(extra_one)
        positions_before = dict(self.queue_a.items.values_list('activity_id', 'position'))
        reconcile_activity(extra_two)
        positions_after = dict(self.queue_a.items.values_list('activity_id', 'position'))

        self.assertEqual(positions_after[self.activity_a.id], consumed_position)
        self.assertLess(positions_after[extra_one.id], positions_after[extra_two.id] + self.queue_a.pool_size)
        self.assertEqual(len(positions_after.values()), len(set(positions_after.values())))
        self.assertEqual(self.queue_a.items.filter(activity=extra_two).count(), 1)
        self.assertEqual(positions_before[self.activity_a.id], positions_after[self.activity_a.id])


class DailyLimitsTests(TestCase):
    def setUp(self):
        self.all_group, _ = Group.objects.get_or_create(name='Todos')
        self.all_group.is_default = True
        self.all_group.max_daily_minutes = 0
        self.all_group.save(update_fields=['is_default', 'max_daily_minutes'])
        self.group = Group.objects.create(name='Limitado', max_daily_minutes=30)
        self.category = Category.objects.create(name='Categoria', group=self.group, max_daily_executions=1)

    def queue_item(self, *, scope, activity):
        queue = ActivityQueue.objects.create(
            scope_key=scope,
            group=self.group,
            pool_size=1,
        )
        return ActivityQueueItem.objects.create(
            queue=queue,
            activity=activity,
            position=1,
            state=ActivityQueueItem.STATE_PRESENTED,
        )

    def test_category_limit_is_consumed_on_start(self):
        first = Activity.objects.create(name='Primeira', category=self.category, duration=10)
        second = Activity.objects.create(name='Segunda', category=self.category, duration=10)
        schedule, _ = start_activity(activity=first, queue_item=self.queue_item(scope='one', activity=first), scope_key='one')
        complete_schedule(schedule)

        with self.assertRaises(ActivityExecutionConflict) as raised:
            start_activity(activity=second, queue_item=self.queue_item(scope='two', activity=second), scope_key='two')
        self.assertEqual(raised.exception.code, 'daily_limit_reached')

    def test_group_limit_and_open_execution_reservation_are_enforced(self):
        self.category.max_daily_executions = 10
        self.category.save(update_fields=['max_daily_executions'])
        first = Activity.objects.create(name='Reserva', category=self.category, duration=20)
        second = Activity.objects.create(name='Nao cabe', category=self.category, duration=15)
        start_activity(activity=first, queue_item=self.queue_item(scope='one', activity=first), scope_key='one')

        second_item = self.queue_item(scope='two', activity=second)
        with self.assertRaises(ActivityExecutionConflict) as raised:
            start_activity(activity=second, queue_item=second_item, scope_key='two')
        self.assertEqual(raised.exception.code, 'group_daily_minutes_reached')

    def test_zero_means_unlimited_and_all_uses_only_its_own_limit(self):
        self.category.max_daily_executions = 10
        self.category.save(update_fields=['max_daily_executions'])
        activity = Activity.objects.create(name='Global', category=self.category, duration=60)
        queue = ActivityQueue.objects.create(scope_key='all', group=self.all_group, pool_size=1)
        item = ActivityQueueItem.objects.create(
            queue=queue,
            activity=activity,
            position=1,
            state=ActivityQueueItem.STATE_PRESENTED,
        )

        schedule, created = start_activity(activity=activity, queue_item=item, scope_key='all')

        self.assertTrue(created)
        self.assertEqual(schedule.expected_end_at - schedule.starts_at, timedelta(minutes=60))
        self.assertEqual(History.objects.filter(schedule=schedule).count(), 1)


class GroupQueueMigrationTests(TransactionTestCase):
    migrate_from = [('pomodoro', '0013_remove_schedule_daily_unique')]
    migrate_to = [('pomodoro', '0014_group_scoped_persistent_queues')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(transaction.get_connection())
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        GroupModel = old_apps.get_model('pomodoro', 'Group')
        QueueModel = old_apps.get_model('pomodoro', 'ActivityQueue')
        default_group, _ = GroupModel.objects.get_or_create(name='Todos')
        default_group.is_default = True
        default_group.save(update_fields=['is_default'])
        self.legacy_queue_id = QueueModel.objects.create(
            scope_key='legacy-scope',
            group=None,
            state='active',
        ).id
        executor = MigrationExecutor(transaction.get_connection())
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(transaction.get_connection())
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_legacy_null_group_is_normalized_before_new_constraint(self):
        QueueModel = self.apps.get_model('pomodoro', 'ActivityQueue')
        GroupModel = self.apps.get_model('pomodoro', 'Group')
        queue = QueueModel.objects.get(pk=self.legacy_queue_id)

        self.assertEqual(queue.group_id, GroupModel.objects.get(is_default=True).id)
        self.assertEqual(queue.state, 'active')
        self.assertTrue(hasattr(queue, 'source_queue_id'))
        self.assertEqual(GroupModel.objects.get(is_default=True).max_daily_minutes, 0)
