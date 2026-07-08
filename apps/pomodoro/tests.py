import importlib

from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey
from datetime import timedelta

from .models import (
    DEFAULT_CATEGORY_ID,
    DEFAULT_CATEGORY_NAME,
    Activity,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
)


class ActivityNextViewSetTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='test-key')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        Group.objects.get_or_create(
            name='Todos',
            defaults={
                'description': 'Grupo padrao que mantem o comportamento atual.',
                'color': '#FFFFFF',
                'is_default': True,
            },
        )

    def _create_history(self, activity, started_at=None, ended_at=None):
        started_at = started_at or timezone.now()
        schedule = Schedule.objects.create(
            activity=activity,
            scheduled_date=started_at.date(),
            start_time=started_at.time(),
            completed=ended_at is not None,
            end_time=ended_at.time() if ended_at else None,
        )
        return History.objects.create(
            activity=activity,
            schedule=schedule,
            start_time=started_at,
            end_time=ended_at,
            duration=25 if ended_at else None,
        )

    def test_next_without_group_filter_keeps_category_rule(self):
        default_group = Group.objects.get(is_default=True)
        category_games = Category.objects.create(
            name='Jogar',
            group=default_group,
            max_daily_executions=1,
        )
        category_study = Category.objects.create(
            name='Estudar',
            group=default_group,
            max_daily_executions=1,
        )
        exhausted_activity = Activity.objects.create(name='Valorant', category=category_games)
        available_activity = Activity.objects.create(name='Python', category=category_study)

        now = timezone.now()
        self._create_history(exhausted_activity, started_at=now, ended_at=now)

        response = self.client.get('/api/activities/next/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], available_activity.id)

    def test_next_with_group_filter_keeps_category_limit_only(self):
        games_group = Group.objects.create(
            name='Jogos',
        )
        games_category = Category.objects.create(
            name='Competitivo',
            group=games_group,
            max_daily_executions=1,
        )
        another_games_category = Category.objects.create(
            name='Casual',
            group=games_group,
            max_daily_executions=5,
        )
        first_activity = Activity.objects.create(name='CS2', category=games_category)
        second_activity = Activity.objects.create(name='FIFA', category=another_games_category)

        now = timezone.now()
        self._create_history(first_activity, started_at=now, ended_at=now)

        response = self.client.get(f'/api/activities/next/?group_id={games_group.id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], second_activity.id)

    def test_list_can_filter_by_group(self):
        default_group = Group.objects.get(is_default=True)
        games_group = Group.objects.create(name='Jogos')
        games_category = Category.objects.create(name='RPG', group=games_group)
        default_category = Category.objects.create(name='Livre', group=default_group)

        games_activity = Activity.objects.create(name='Zelda', category=games_category)
        Activity.objects.create(name='Caminhar', category=default_category)

        response = self.client.get(f'/api/activities/?group_id={games_group.id}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], games_activity.id)

    def test_inactive_activity_does_not_appear_in_list_or_next(self):
        default_group = Group.objects.get(is_default=True)
        category = Category.objects.create(name='Backlog', group=default_group, max_daily_executions=5)
        Activity.objects.create(name='Jogo Desinstalado', category=category, active=False)
        active_activity = Activity.objects.create(name='Jogo Instalado', category=category, active=True)

        list_response = self.client.get('/api/activities/')
        next_response = self.client.get('/api/activities/next/')

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]['id'], active_activity.id)
        self.assertEqual(next_response.status_code, status.HTTP_200_OK)
        self.assertEqual(next_response.data['id'], active_activity.id)

    def test_next_prioritizes_premium_activity(self):
        default_group = Group.objects.get(is_default=True)
        category = Category.objects.create(name='Online', group=default_group, max_daily_executions=5)
        today = timezone.localdate()

        normal_activity = Activity.objects.create(name='Jogo Normal', category=category)
        premium_activity = Activity.objects.create(
            name='Evento Premium',
            category=category,
            premium=True,
            premium_from=today,
            premium_until=today + timedelta(days=3),
        )

        response = self.client.get('/api/activities/next/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], premium_activity.id)
        self.assertTrue(response.data['premium'])
        self.assertTrue(response.data['is_premium_active'])
        self.assertNotEqual(response.data['id'], normal_activity.id)

    def test_expired_premium_is_disabled_automatically_when_requesting_tasks(self):
        default_group = Group.objects.get(is_default=True)
        category = Category.objects.create(name='Eventos', group=default_group, max_daily_executions=5)
        today = timezone.localdate()

        expired_activity = Activity.objects.create(
            name='Passe Expirado',
            category=category,
            premium=True,
            premium_from=today - timedelta(days=10),
            premium_until=today - timedelta(days=1),
        )

        response = self.client.get('/api/activities/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expired_activity.refresh_from_db()
        self.assertFalse(expired_activity.premium)
        self.assertEqual(response.data[0]['premium'], False)

    def test_activity_created_without_category_uses_default_category(self):
        activity = Activity.objects.create(name='Sem Categoria')

        self.assertEqual(activity.category_id, DEFAULT_CATEGORY_ID)
        self.assertEqual(activity.category.name, DEFAULT_CATEGORY_NAME)

    def test_default_category_cannot_be_removed(self):
        default_category = Category.objects.get(pk=DEFAULT_CATEGORY_ID)

        with self.assertRaises(ValidationError):
            default_category.delete()

    def test_list_and_next_never_return_null_category(self):
        activity = Activity.objects.create(name='Categoria Default')

        list_response = self.client.get('/api/activities/')
        next_response = self.client.get('/api/activities/next/')

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data[0]['category'], DEFAULT_CATEGORY_ID)
        self.assertEqual(next_response.status_code, status.HTTP_200_OK)
        self.assertEqual(next_response.data['category'], DEFAULT_CATEGORY_ID)
        self.assertEqual(next_response.data['activity']['category'], DEFAULT_CATEGORY_ID)


class ActivityQueueAndExecutionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='queue-key')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')
        self.default_group, _ = Group.objects.get_or_create(
            name='Todos',
            defaults={
                'description': 'Grupo padrao que mantem o comportamento atual.',
                'color': '#FFFFFF',
                'is_default': True,
            },
        )
        self.category = Category.objects.create(
            name='Foco',
            group=self.default_group,
            max_daily_executions=10,
        )

    def _create_activity(self, name, duration=25):
        return Activity.objects.create(
            name=name,
            category=self.category,
            duration=duration,
        )

    def test_next_returns_persisted_queue_item_and_repeats_until_consumed(self):
        self._create_activity('Primeira')
        self._create_activity('Segunda')

        response_one = self.client.get('/api/activities/next/')
        response_two = self.client.get('/api/activities/next/')

        self.assertEqual(response_one.status_code, status.HTTP_200_OK)
        self.assertEqual(response_one.data['queue_item_id'], response_two.data['queue_item_id'])
        self.assertEqual(response_one.data['queue_id'], response_two.data['queue_id'])
        self.assertEqual(response_one.data['id'], response_one.data['activity']['id'])
        self.assertEqual(response_one.data['activity']['id'], response_two.data['activity']['id'])

    def test_skip_marks_item_and_returns_next_item_id(self):
        self._create_activity('Primeira')
        self._create_activity('Segunda')

        next_response = self.client.get('/api/activities/next/')
        queue_item_id = next_response.data['queue_item_id']
        skipped_activity_id = next_response.data['activity']['id']

        skip_response = self.client.post(f'/api/activity-queue/items/{queue_item_id}/skip/')
        after_skip = self.client.get('/api/activities/next/')

        self.assertEqual(skip_response.status_code, status.HTTP_200_OK)
        self.assertEqual(skip_response.data['activity_id'], skipped_activity_id)
        self.assertEqual(skip_response.data['state'], 'skipped')
        self.assertEqual(after_skip.status_code, status.HTTP_200_OK)
        self.assertNotEqual(after_skip.data['id'], skipped_activity_id)

    def test_start_creates_persistent_execution_and_active_restores_it(self):
        activity = self._create_activity('Persistente')
        next_response = self.client.get('/api/activities/next/')

        start_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )
        active_response = self.client.get('/api/activities/active/')

        self.assertEqual(start_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(start_response.data['state'], 'running')
        self.assertEqual(active_response.status_code, status.HTTP_200_OK)
        self.assertEqual(active_response.data['queue_item_id'], next_response.data['queue_item_id'])
        self.assertEqual(active_response.data['activity']['id'], activity.id)

    def test_start_conflicts_when_another_execution_is_already_running(self):
        first = self._create_activity('Primeira')
        second = self._create_activity('Segunda')
        first_next = self.client.get('/api/activities/next/')
        current_activity_id = first_next.data['activity']['id']
        current_activity = first if first.id == current_activity_id else second
        other_activity = second if current_activity.id == first.id else first

        first_start = self.client.post(
            f'/api/activities/{current_activity.id}/start/',
            {'queue_item_id': first_next.data['queue_item_id']},
            format='json',
        )
        self.assertEqual(first_start.status_code, status.HTTP_201_CREATED)

        second_queue_item = ActivityQueueItem.objects.get(
            queue_id=first_next.data['queue_id'],
            activity=other_activity,
        )
        response = self.client.post(
            f'/api/activities/{other_activity.id}/start/',
            {'queue_item_id': second_queue_item.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['code'], 'active_execution_conflict')
        self.assertEqual(response.data['active_execution']['activity']['id'], current_activity.id)

    def test_active_reconciles_expired_execution_to_no_content(self):
        activity = self._create_activity('Curta', duration=1)
        next_response = self.client.get('/api/activities/next/')
        start_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )

        schedule = Schedule.objects.get(pk=start_response.data['schedule_id'])
        schedule.expected_end_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=['expected_end_at'])

        active_response = self.client.get('/api/activities/active/')
        schedule.refresh_from_db()

        self.assertEqual(active_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(schedule.state, Schedule.STATE_COMPLETED)
        self.assertTrue(schedule.completed)

    def test_status_and_reconcile_return_completed_execution_after_expiration(self):
        activity = self._create_activity('Reconcilia', duration=1)
        next_response = self.client.get('/api/activities/next/')
        start_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )
        schedule_id = start_response.data['schedule_id']

        schedule = Schedule.objects.get(pk=schedule_id)
        schedule.expected_end_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=['expected_end_at'])

        status_response = self.client.get(f'/api/activities/status/{schedule_id}/')
        reconcile_response = self.client.post(f'/api/activity-executions/{schedule_id}/reconcile/')

        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data['state'], 'completed')
        self.assertEqual(reconcile_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reconcile_response.data['state'], 'completed')

    def test_start_is_idempotent_for_same_queue_item(self):
        activity = self._create_activity('Idempotente')
        next_response = self.client.get('/api/activities/next/')
        payload = {'queue_item_id': next_response.data['queue_item_id']}

        first_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            payload,
            format='json',
        )
        second_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            payload,
            format='json',
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data['schedule_id'], second_response.data['schedule_id'])
        self.assertEqual(first_response.data['activity_id'], second_response.data['activity_id'])
        self.assertEqual(second_response.data['status'], 'already_started')
        self.assertEqual(History.objects.filter(schedule_id=first_response.data['schedule_id']).count(), 1)

    def test_start_allows_same_activity_again_after_completed_execution_on_same_day(self):
        activity = self._create_activity('Repetivel')
        next_response = self.client.get('/api/activities/next/')
        first_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )
        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)

        schedule = Schedule.objects.get(pk=first_response.data['schedule_id'])
        complete_response = self.client.post(
            '/api/activities/complete/',
            {'schedule_id': schedule.id},
            format='json',
        )
        self.assertEqual(complete_response.status_code, status.HTTP_200_OK)

        second_queue_item = ActivityQueueItem.objects.create(
            queue=schedule.queue_item.queue,
            activity=activity,
            position=schedule.queue_item.position + 1,
            state=ActivityQueueItem.STATE_PRESENTED,
        )
        second_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': second_queue_item.id},
            format='json',
        )

        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(second_response.data['schedule_id'], first_response.data['schedule_id'])
        self.assertEqual(
            History.objects.filter(activity=activity).count(),
            2,
        )

    def test_start_returns_conflict_when_queue_item_was_already_consumed(self):
        activity = self._create_activity('Consumida')
        next_response = self.client.get('/api/activities/next/')

        first_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )
        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)

        schedule = Schedule.objects.get(pk=first_response.data['schedule_id'])
        self.client.post(
            '/api/activities/complete/',
            {'schedule_id': schedule.id},
            format='json',
        )

        retry_response = self.client.post(
            f'/api/activities/{activity.id}/start/',
            {'queue_item_id': next_response.data['queue_item_id']},
            format='json',
        )

        self.assertEqual(retry_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(retry_response.data['code'], 'queue_item_unavailable')

    def test_next_skips_stale_presented_item_with_completed_schedule(self):
        first = self._create_activity('Primeira')
        second = self._create_activity('Segunda')

        next_response = self.client.get('/api/activities/next/')
        first_queue_item = ActivityQueueItem.objects.get(pk=next_response.data['queue_item_id'])
        stale_activity = first if first.id == first_queue_item.activity_id else second
        remaining_activity = second if stale_activity.id == first.id else first
        first_queue_item.state = ActivityQueueItem.STATE_PRESENTED
        first_queue_item.save(update_fields=['state'])

        schedule = Schedule.objects.create(
            activity=stale_activity,
            scheduled_date=timezone.now().date(),
            start_time=timezone.now().time(),
            completed=True,
            end_time=timezone.now().time(),
            queue_item=first_queue_item,
            scope_key='completed-scope',
            state=Schedule.STATE_COMPLETED,
            version=1,
            requested_at=timezone.now(),
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )
        History.objects.create(
            activity=stale_activity,
            schedule=schedule,
            start_time=timezone.now() - timedelta(minutes=60),
            end_time=timezone.now(),
            duration=60,
        )

        response = self.client.get('/api/activities/next/')
        first_queue_item.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['activity']['id'], remaining_activity.id)
        self.assertEqual(first_queue_item.state, ActivityQueueItem.STATE_COMPLETED)


class DefaultCategoryMigrationTests(APITestCase):
    def test_migration_relocates_category_occupying_id_one(self):
        Activity.objects.all().delete()
        Category.objects.exclude(pk=DEFAULT_CATEGORY_ID).delete()
        Group.objects.exclude(is_default=True).delete()

        default_group = Group.objects.filter(is_default=True).first()
        if default_group is None:
            default_group = Group.objects.create(
                name='Todos',
                description='Grupo padrao',
                color='#FFFFFF',
                is_default=True,
            )

        default_category = Category.objects.filter(pk=DEFAULT_CATEGORY_ID).first()
        if default_category is None:
            default_category = Category.objects.create(
                id=DEFAULT_CATEGORY_ID,
                name=DEFAULT_CATEGORY_NAME,
                description='Categoria padrao',
                color='#FFFFFF',
                max_daily_executions=2,
                executions_today=0,
                group=default_group,
            )
        else:
            default_category.name = 'Estudo'
            default_category.description = 'Categoria original'
            default_category.color = '#123456'
            default_category.max_daily_executions = 5
            default_category.group = default_group
            default_category.save(
                update_fields=['name', 'description', 'color', 'max_daily_executions', 'group']
            )

        default_group = Group.objects.get(pk=default_group.pk)
        occupied = Category.objects.get(pk=DEFAULT_CATEGORY_ID)
        activity = Activity.objects.create(
            name='Ler',
            category=occupied,
        )

        migration_module = importlib.import_module(
            'apps.pomodoro.migrations.0012_default_category_contract'
        )

        class AppsShim:
            @staticmethod
            def get_model(app_label, model_name):
                mapping = {
                    ('pomodoro', 'Category'): Category,
                    ('pomodoro', 'Activity'): Activity,
                    ('pomodoro', 'Group'): Group,
                }
                return mapping[(app_label, model_name)]

        class ConnectionOpsShim:
            @staticmethod
            def sequence_reset_sql(*args, **kwargs):
                return []

        class ConnectionShim:
            ops = ConnectionOpsShim()

        class SchemaEditorShim:
            connection = ConnectionShim()

            @staticmethod
            def execute(sql):
                return None

        migration_module.ensure_default_category(AppsShim(), SchemaEditorShim())

        activity.refresh_from_db()
        relocated = Category.objects.get(name='Estudo')
        default_category = Category.objects.get(pk=DEFAULT_CATEGORY_ID)

        self.assertNotEqual(relocated.pk, DEFAULT_CATEGORY_ID)
        self.assertEqual(activity.category_id, relocated.pk)
        self.assertEqual(default_category.name, DEFAULT_CATEGORY_NAME)
