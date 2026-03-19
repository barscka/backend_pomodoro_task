from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import Activity, Category, Group, History, Schedule


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
