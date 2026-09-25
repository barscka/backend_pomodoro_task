import hashlib
from datetime import timedelta
from uuid import uuid4

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .admin import RetroGameAdmin, RetroGameProgressAdmin, RetroPlatformAdmin
from .models import (Activity, ActivityPreferenceEvent, ActivityQueue, Category,
                     GoalCompletion, Group, History, RetroGame, RetroGameProgress,
                     RetroPlatform, Schedule)
from .services.activity_queue import (category_started_count, eligible_activities,
                                      group_reserved_minutes)


class RetroFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.retro_group = Group.objects.get(is_retro_catalog=True)
        cls.generation = Category.objects.create(
            name='4ª geração', group=cls.retro_group, retro_sort_order=4,
            max_daily_executions=1,
        )
        cls.platform = RetroPlatform.objects.create(
            generation=cls.generation, name='Super Nintendo', slug='snes',
            release_year=1990, sort_order=1,
        )
        cls.activity = Activity.objects.create(
            name='Super Mario World', category=cls.generation, duration=60,
        )
        cls.game = RetroGame.objects.create(
            activity=cls.activity, platform=cls.platform, tier='essential',
            estimated_main_minutes=360, release_year=1990, sort_order=1,
        )


class RetroModelAndAdminTests(RetroFixtures, TestCase):
    def test_single_catalog_and_hierarchy_validation(self):
        duplicate = Group(name='Outro catálogo', is_retro_catalog=True)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        ordinary = Group.objects.create(name='Comum')
        invalid_generation = Category.objects.create(name='Inválida', group=ordinary)
        platform = RetroPlatform(
            generation=invalid_generation, name='PC', slug='pc-retro', sort_order=1
        )
        with self.assertRaises(ValidationError):
            platform.full_clean()

        other_generation = Category.objects.create(
            name='5ª geração', group=self.retro_group, retro_sort_order=5
        )
        other_activity = Activity.objects.create(name='Outro jogo', category=other_generation)
        invalid_game = RetroGame(
            activity=other_activity, platform=self.platform, tier='essential',
            estimated_main_minutes=60, sort_order=1,
        )
        with self.assertRaises(ValidationError):
            invalid_game.full_clean()

    def test_model_constraints_and_protected_relations(self):
        with self.assertRaises(ValidationError):
            RetroGame(
                activity=Activity(name='Zero', category=self.generation),
                platform=self.platform, tier='essential', estimated_main_minutes=0,
                sort_order=0,
            ).full_clean()
        with self.assertRaises(Exception):
            self.activity.delete()
        with self.assertRaises(Exception):
            self.platform.delete()

    def test_schedule_retro_context_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Schedule.objects.create(
                    activity=self.activity, scheduled_date=timezone.localdate(),
                    start_time=timezone.localtime().time(), scope_key='invalid-retro',
                    state=Schedule.STATE_RUNNING,
                    execution_origin=Schedule.ORIGIN_RETRO_DIRECT,
                )

    def test_admin_configuration_supports_editorial_workflow(self):
        platform_admin = RetroPlatformAdmin(RetroPlatform, AdminSite())
        game_admin = RetroGameAdmin(RetroGame, AdminSite())
        progress_admin = RetroGameProgressAdmin(RetroGameProgress, AdminSite())
        self.assertIn('slug', platform_admin.search_fields)
        self.assertIn('platform__generation', game_admin.list_filter)
        self.assertNotIn('scope_key', progress_admin.list_display)
        self.assertEqual(game_admin.autocomplete_fields, ('activity', 'platform'))


@override_settings(RETROGAMES_ENABLED=True)
class RetroApiTests(RetroFixtures, APITestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _, cls.key = APIKey.objects.create_key(name='spec-015')
        _, cls.other_key = APIKey.objects.create_key(name='spec-015-other')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')

    @property
    def scope(self):
        return hashlib.sha256(f'Api-Key {self.key}'.encode()).hexdigest()

    def test_catalog_order_filters_search_and_pagination(self):
        old_generation = Category.objects.create(
            name='3ª geração', group=self.retro_group, retro_sort_order=3
        )
        old_platform = RetroPlatform.objects.create(
            generation=old_generation, name='NES', slug='nes', release_year=1983,
            sort_order=1,
        )
        old_activity = Activity.objects.create(name='Zelda', category=old_generation)
        old_game = RetroGame.objects.create(
            activity=old_activity, platform=old_platform, tier='complementary',
            estimated_main_minutes=300, sort_order=1,
        )
        generations = self.client.get('/api/retro-generations/')
        self.assertEqual([row['sort_order'] for row in generations.data], [3, 4])
        platforms = self.client.get(f'/api/retro-platforms/?generation_id={old_generation.id}')
        self.assertEqual([row['slug'] for row in platforms.data], ['nes'])
        games = self.client.get('/api/retro-games/?page_size=1')
        self.assertEqual(games.status_code, 200)
        self.assertEqual(games.data['count'], 2)
        self.assertEqual(games.data['results'][0]['id'], old_game.id)
        filtered = self.client.get('/api/retro-games/?tier=essential&search=Mario')
        self.assertEqual([row['id'] for row in filtered.data['results']], [self.game.id])

    def test_progress_is_versioned_idempotent_and_isolated_by_scope(self):
        first = self.client.patch(
            f'/api/retro-games/{self.game.id}/progress/',
            {'status': 'completed', 'expected_version': 0}, format='json',
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data['version'], 1)
        self.assertIsNotNone(first.data['completed_at'])
        replay = self.client.patch(
            f'/api/retro-games/{self.game.id}/progress/',
            {'status': 'completed', 'expected_version': 0}, format='json',
        )
        self.assertEqual(replay.data['version'], 1)
        stale = self.client.patch(
            f'/api/retro-games/{self.game.id}/progress/',
            {'status': 'skipped', 'expected_version': 0}, format='json',
        )
        self.assertEqual((stale.status_code, stale.data['code']), (409, 'stale_progress_version'))
        reopened = self.client.patch(
            f'/api/retro-games/{self.game.id}/progress/',
            {'status': 'in_progress', 'expected_version': 1}, format='json',
        )
        self.assertEqual(reopened.data['version'], 2)
        self.assertIsNone(reopened.data['completed_at'])

        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.other_key}')
        detail = self.client.get(f'/api/retro-games/{self.game.id}/')
        self.assertEqual(detail.data['status'], 'not_started')
        self.assertNotIn('scope_key', detail.data)

    def test_played_time_partial_coverage_and_percent_above_100(self):
        GoalCompletion.objects.create(
            source_schedule_id=9001, scope_key=self.scope,
            category_id_snapshot=self.generation.id, group_id_snapshot=self.retro_group.id,
            completed_at=timezone.now(), duration_minutes=400,
            activity_id_snapshot=self.activity.id, activity_name_snapshot=self.activity.name,
            duration_seconds=None, execution_origin='legacy', context_source='legacy_current',
        )
        detail = self.client.get(f'/api/retro-games/{self.game.id}/')
        self.assertEqual(detail.data['played_seconds'], 24000)
        self.assertEqual(detail.data['coverage'], 'partial')
        self.assertGreater(detail.data['progress_percent'], 100)
        self.assertEqual(detail.data['status'], 'in_progress')
        filtered = self.client.get('/api/retro-games/?status=in_progress')
        self.assertEqual([row['id'] for row in filtered.data['results']], [self.game.id])

    def test_open_execution_estimate_is_separate_from_consolidated_time(self):
        start = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 30, 'request_id': str(uuid4())}, format='json',
        )
        schedule = Schedule.objects.get(pk=start.data['execution_id'])
        schedule.starts_at = timezone.now() - timedelta(minutes=10)
        schedule.save(update_fields=['starts_at'])
        detail = self.client.get(f'/api/retro-games/{self.game.id}/')
        self.assertGreaterEqual(detail.data['open_estimate_seconds'], 599)
        self.assertEqual(detail.data['played_seconds'], 0)

    def test_inactive_and_invalid_hierarchy_have_stable_errors(self):
        self.game.active = False
        self.game.save(update_fields=['active'])
        inactive = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 30, 'request_id': str(uuid4())}, format='json',
        )
        self.assertEqual((inactive.status_code, inactive.data['code']), (422, 'retro_game_inactive'))
        self.game.active = True
        other_generation = Category.objects.create(
            name='Geração divergente API', group=self.retro_group, retro_sort_order=8
        )
        self.activity.category = other_generation
        self.activity.save(update_fields=['category'])
        invalid = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 30, 'request_id': str(uuid4())}, format='json',
        )
        self.assertEqual((invalid.status_code, invalid.data['code']), (400, 'retro_hierarchy_invalid'))

    def test_start_replay_conflict_active_restore_and_no_queue_side_effects(self):
        queue = ActivityQueue.objects.create(
            group=self.retro_group, scope_key=self.scope, pool_size=0
        )
        request_id = str(uuid4())
        payload = {'duration_minutes': 30, 'request_id': request_id,
                   'return_group_id': self.retro_group.id}
        first = self.client.post(f'/api/retro-games/{self.game.id}/start/', payload, format='json')
        replay = self.client.post(f'/api/retro-games/{self.game.id}/start/', payload, format='json')
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.data['execution_id'], replay.data['execution_id'])
        self.assertEqual(first.data['execution_origin'], 'retro_direct')
        self.assertEqual(first.data['retro_game_id'], self.game.id)
        self.assertIsNone(first.data['queue_item_id'])
        self.assertIsNone(first.data['premium_period_id'])
        self.assertEqual(first.data['planned_duration_seconds'], 1800)
        active = self.client.get('/api/activities/active/')
        self.assertEqual(active.data['retro_game_id'], self.game.id)
        conflict = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 60, 'request_id': request_id}, format='json',
        )
        self.assertEqual(conflict.data['code'], 'idempotency_payload_conflict')
        another = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 30, 'request_id': str(uuid4())}, format='json',
        )
        self.assertEqual(another.data['code'], 'active_execution_conflict')
        self.assertEqual(ActivityQueue.objects.get(pk=queue.pk).state, 'active')
        self.assertEqual(category_started_count(self.generation), 0)
        self.assertEqual(group_reserved_minutes(self.retro_group), 0)
        self.assertFalse(ActivityPreferenceEvent.objects.exists())
        self.assertEqual(self.activity.executions_today, 0)

    def test_completion_goal_fact_and_continuation_are_idempotent(self):
        start = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 15, 'request_id': str(uuid4())}, format='json',
        )
        completed = self.client.post(
            '/api/activities/complete/', {'schedule_id': start.data['execution_id']}, format='json'
        )
        repeated = self.client.post(
            '/api/activities/complete/', {'schedule_id': start.data['execution_id']}, format='json'
        )
        self.assertEqual((completed.status_code, repeated.status_code), (200, 200))
        self.assertEqual(GoalCompletion.objects.count(), 1)
        continuation_key = str(uuid4())
        payload = {'duration_minutes': 20, 'request_id': continuation_key,
                   'expected_version': completed.data['version']}
        continued = self.client.post(
            f'/api/activity-executions/{start.data["execution_id"]}/continue/', payload, format='json'
        )
        replay = self.client.post(
            f'/api/activity-executions/{start.data["execution_id"]}/continue/', payload, format='json'
        )
        self.assertEqual((continued.status_code, replay.status_code), (201, 200))
        self.assertEqual(continued.data['continued_from_id'], start.data['execution_id'])
        self.assertEqual(continued.data['retro_game_id'], self.game.id)

    def test_completed_or_skipped_progress_is_not_reopened_by_start(self):
        progress = RetroGameProgress.objects.create(
            scope_key=self.scope, retro_game=self.game, status='completed',
            started_at=timezone.now() - timedelta(days=1), completed_at=timezone.now(),
        )
        response = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 10, 'request_id': str(uuid4())}, format='json',
        )
        self.assertEqual(response.status_code, 201)
        progress.refresh_from_db()
        self.assertEqual(progress.status, 'completed')

    def test_sessions_are_scoped_and_paginated(self):
        for index in range(3):
            GoalCompletion.objects.create(
                source_schedule_id=9100 + index, scope_key=self.scope,
                category_id_snapshot=self.generation.id, group_id_snapshot=self.retro_group.id,
                completed_at=timezone.now() + timedelta(seconds=index), duration_minutes=1,
                activity_id_snapshot=self.activity.id, activity_name_snapshot=self.activity.name,
                duration_seconds=60, execution_origin='retro_direct',
                context_source='execution_start',
            )
        response = self.client.get(f'/api/retro-games/{self.game.id}/sessions/?page_size=2')
        self.assertEqual(response.data['count'], 3)
        self.assertEqual(len(response.data['results']), 2)
        self.assertTrue(all('scope_key' not in row for row in response.data['results']))

    def test_direct_session_does_not_remove_game_from_queue_eligibility(self):
        started = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 5, 'request_id': str(uuid4())}, format='json',
        )
        self.client.post('/api/activities/complete/', {'schedule_id': started.data['execution_id']}, format='json')
        self.assertIn(self.activity, list(eligible_activities(selected_group=self.retro_group)))


class RetroFeatureFlagTests(RetroFixtures, APITestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _, cls.key = APIKey.objects.create_key(name='spec-015-disabled')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')

    @override_settings(RETROGAMES_ENABLED=False)
    def test_catalog_remains_readable_but_start_is_disabled(self):
        detail = self.client.get(f'/api/retro-games/{self.game.id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.data['can_start'])
        started = self.client.post(
            f'/api/retro-games/{self.game.id}/start/',
            {'duration_minutes': 30, 'request_id': str(uuid4())}, format='json',
        )
        self.assertEqual((started.status_code, started.data['code']), (503, 'retrogames_disabled'))
