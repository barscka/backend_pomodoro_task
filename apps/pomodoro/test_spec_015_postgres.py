from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from .models import Activity, Category, Group, RetroGame, RetroPlatform, Schedule
from .services.activity_execution import ActivityExecutionConflict
from .services.retro_execution import start_retro


@skipUnlessDBFeature('has_select_for_update')
@override_settings(RETROGAMES_ENABLED=True)
class RetroPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        group = Group.objects.get(is_retro_catalog=True)
        generation = Category.objects.create(name='Retro concorrente', group=group, retro_sort_order=1)
        platform = RetroPlatform.objects.create(generation=generation, name='Console', slug='console', sort_order=1)
        activity = Activity.objects.create(name='Jogo concorrente', category=generation)
        self.game = RetroGame.objects.create(activity=activity, platform=platform, tier='essential',
                                             estimated_main_minutes=60, sort_order=1)

    def _start(self, request_id):
        close_old_connections()
        try:
            schedule, created = start_retro(retro_game_id=self.game.id,
                                            scope_key='retro-concurrent-scope',
                                            duration_minutes=30, request_id=request_id)
            return ('ok', schedule.id, created)
        except ActivityExecutionConflict as exc:
            return ('conflict', exc.code)
        finally:
            close_old_connections()

    def test_two_devices_create_only_one_open_execution(self):
        self.assertEqual(connection.vendor, 'postgresql')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self._start, [uuid4(), uuid4()]))
        self.assertEqual(Schedule.objects.filter(scope_key='retro-concurrent-scope', state='running').count(), 1)
        self.assertEqual(sum(result[0] == 'ok' for result in results), 1)
        self.assertIn(('conflict', 'active_execution_conflict'), results)
