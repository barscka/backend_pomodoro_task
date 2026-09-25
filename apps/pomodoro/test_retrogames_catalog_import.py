import json
from datetime import date, time
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import (
    Activity, Category, Group, History, RetroGame, RetroGameProgress, RetroPlatform, Schedule,
)
from .services.retrogames_catalog_import import (
    CATALOG_SOURCE,
    CatalogValidationError,
    compare_inventory,
    import_catalog,
    load_catalog,
    validate_catalog,
)


CATALOG_PATH = Path(__file__).parent / 'data' / 'retrogames_catalog.json'


def minimal_catalog():
    return {
        'schema_version': 1,
        'catalog_version': 'test',
        'total_estimated_minutes': 120,
        'group': {'name': 'Retrogames', 'description': 'Teste', 'color': '#123456'},
        'generations': [{
            'key': 'generation-4', 'name': '4ª geração', 'sort_order': 4,
            'platforms': [{
                'slug': 'console', 'name': 'Console', 'manufacturer': 'Fabricante',
                'release_year': 1990, 'sort_order': 1, 'active': True,
                'games': [{
                    'key': 'console-same-name', 'name': 'Same Name', 'release_year': 1991,
                    'tier': 'essential', 'estimated_main_minutes': 120,
                    'default_block_minutes': 30, 'play_goal': 'Campanha',
                    'sort_order': 1, 'active': True, 'aliases': ['Alias Name'],
                }],
            }],
        }],
    }


class CatalogValidationTests(SimpleTestCase):
    def test_versioned_catalog_is_valid_and_has_expected_counts(self):
        catalog = load_catalog(CATALOG_PATH)
        validate_catalog(catalog)
        self.assertEqual(len(catalog['generations']), 8)
        self.assertEqual(sum(len(item['platforms']) for item in catalog['generations']), 23)
        self.assertEqual(
            sum(len(platform['games']) for item in catalog['generations'] for platform in item['platforms']),
            116,
        )
        self.assertEqual(catalog['total_estimated_minutes'], 60060)

    def test_rejects_invalid_schema_missing_field_duplicates_and_ranges_with_paths(self):
        catalog = minimal_catalog()
        catalog['schema_version'] = 2
        duplicate = deepcopy(catalog['generations'][0]['platforms'][0]['games'][0])
        duplicate.pop('name')
        duplicate['estimated_main_minutes'] = 0
        duplicate['default_block_minutes'] = 721
        duplicate['sort_order'] = 1
        catalog['generations'][0]['platforms'][0]['games'].append(duplicate)
        catalog['total_estimated_minutes'] = 0

        with self.assertRaises(CatalogValidationError) as raised:
            validate_catalog(catalog)

        message = str(raised.exception)
        self.assertIn('$.schema_version', message)
        self.assertIn('generations[0].platforms[0].games[1].name', message)
        self.assertIn('generations[0].platforms[0].games[1].key: chave duplicada', message)
        self.assertIn('estimated_main_minutes', message)
        self.assertIn('default_block_minutes', message)
        self.assertIn('sort_order: ordem duplicada', message)

    def test_rejects_invalid_json(self):
        with NamedTemporaryFile('w', suffix='.json', encoding='utf-8') as handle:
            handle.write('{invalid')
            handle.flush()
            with self.assertRaisesRegex(Exception, 'JSON inválido'):
                load_catalog(handle.name)

    def test_inventory_exact_alias_possible_and_technical_files(self):
        catalog = minimal_catalog()
        games = catalog['generations'][0]['platforms'][0]['games']
        games.extend([
            {**deepcopy(games[0]), 'key': 'console-alias', 'name': 'Editorial Name', 'aliases': ['Alias Name'], 'sort_order': 2},
            {**deepcopy(games[0]), 'key': 'console-possible', 'name': 'Almost Matching Game', 'aliases': [], 'sort_order': 3},
            {**deepcopy(games[0]), 'key': 'console-missing', 'name': 'Definitely Absent', 'aliases': [], 'sort_order': 4},
        ])
        catalog['total_estimated_minutes'] = 480
        with NamedTemporaryFile('w', encoding='utf-8') as handle:
            handle.write('./roms/Console/Same Name (USA).zip\n')
            handle.write('./roms/Console/Alias Name [Europe].rom\n')
            handle.write('./roms/Console/Almost Matchin Game.zip\n')
            handle.write('./BIOS/Definitely Absent.zip\n')
            handle.write('./saves/Definitely Absent.sav\n')
            handle.flush()
            counts, warnings = compare_inventory(catalog, handle.name)
        self.assertEqual(counts, {'confirmed': 1, 'confirmed_by_alias': 1, 'possible': 1, 'not_found': 1})
        self.assertEqual(len(warnings), 2)


class CatalogImportTests(TestCase):
    def setUp(self):
        self.catalog = minimal_catalog()
        self.catalog_file = NamedTemporaryFile('w', suffix='.json', encoding='utf-8')
        self.addCleanup(self.catalog_file.close)
        self._write_catalog()

    def _write_catalog(self):
        self.catalog_file.seek(0)
        self.catalog_file.truncate()
        json.dump(self.catalog, self.catalog_file)
        self.catalog_file.flush()

    def test_first_import_second_import_and_dry_run_are_idempotent(self):
        dry = import_catalog(catalog_path=self.catalog_file.name, dry_run=True)
        self.assertEqual((dry.group.created, dry.generations.created, dry.platforms.created, dry.games.created), (0, 1, 1, 1))
        before = (Group.objects.count(), Category.objects.count(), Activity.objects.count(), RetroPlatform.objects.count(), RetroGame.objects.count())
        self.assertEqual(before[2:], (0, 0, 0))

        first = import_catalog(catalog_path=self.catalog_file.name)
        counts = (Group.objects.count(), Category.objects.count(), Activity.objects.count(), RetroPlatform.objects.count(), RetroGame.objects.count())
        second = import_catalog(catalog_path=self.catalog_file.name)

        self.assertEqual((first.generations.created, first.platforms.created, first.games.created), (1, 1, 1))
        self.assertEqual((second.group.unchanged, second.generations.unchanged, second.platforms.unchanged, second.games.unchanged), (1, 1, 1, 1))
        self.assertEqual(counts, (Group.objects.count(), Category.objects.count(), Activity.objects.count(), RetroPlatform.objects.count(), RetroGame.objects.count()))

    def test_updates_managed_fields_corrects_hierarchy_and_preserves_operational_data(self):
        import_catalog(catalog_path=self.catalog_file.name)
        activity = Activity.objects.get(external_source=CATALOG_SOURCE)
        game = activity.retro_game
        other = Category.objects.create(name='Manual', group=Group.objects.get(is_retro_catalog=True))
        activity.category = other
        activity.premium = True
        activity.premium_from = activity.premium_until = __import__('datetime').date.today()
        activity.priority = 9
        activity.executions_today = 7
        activity.save()
        RetroGameProgress.objects.create(scope_key='scope', retro_game=game, status='in_progress')
        schedule = Schedule.objects.create(
            activity=activity, scheduled_date=date.today(), start_time=time(12),
            state=Schedule.STATE_COMPLETED, execution_origin=Schedule.ORIGIN_LEGACY,
        )
        History.objects.create(activity=activity, schedule=schedule, start_time=timezone.now())
        executions_before_import = Activity.objects.get(pk=activity.pk).executions_today
        self.catalog['generations'][0]['platforms'][0]['games'][0].update(
            name='Edited Name', estimated_main_minutes=180, default_block_minutes=45,
        )
        self.catalog['total_estimated_minutes'] = 180
        self._write_catalog()

        report = import_catalog(catalog_path=self.catalog_file.name)

        activity.refresh_from_db()
        game.refresh_from_db()
        self.assertEqual(report.games.updated, 1)
        self.assertEqual((activity.name, activity.duration, activity.category.name), ('Edited Name', 45, '4ª geração'))
        self.assertEqual((activity.priority, activity.executions_today, activity.premium), (9, executions_before_import, True))
        self.assertEqual(game.estimated_main_minutes, 180)
        self.assertEqual((History.objects.count(), RetroGameProgress.objects.count()), (1, 1))

    def test_same_game_name_on_two_platforms_uses_external_key(self):
        platform = deepcopy(self.catalog['generations'][0]['platforms'][0])
        platform.update(slug='other-console', name='Other Console', sort_order=2)
        platform['games'][0].update(key='other-console-same-name')
        self.catalog['generations'][0]['platforms'].append(platform)
        self.catalog['total_estimated_minutes'] = 240
        self._write_catalog()

        import_catalog(catalog_path=self.catalog_file.name)

        self.assertEqual(Activity.objects.filter(name='Same Name').count(), 2)
        self.assertEqual(RetroGame.objects.values('platform_id').distinct().count(), 2)

    def test_does_not_claim_unmanaged_activity_with_same_name(self):
        manual_group = Group.objects.create(name='Manual')
        manual_category = Category.objects.create(name='Manual', group=manual_group)
        manual = Activity.objects.create(name='Same Name', category=manual_category)

        import_catalog(catalog_path=self.catalog_file.name)

        manual.refresh_from_db()
        self.assertEqual(manual.category, manual_category)
        self.assertEqual(manual.external_source, '')
        self.assertEqual(Activity.objects.filter(name='Same Name').count(), 2)

    def test_missing_is_orphaned_then_only_managed_records_are_deactivated(self):
        import_catalog(catalog_path=self.catalog_file.name)
        activity = Activity.objects.get(external_source=CATALOG_SOURCE)
        game = activity.retro_game
        self.catalog['generations'][0]['platforms'][0]['games'][0].update(
            key='console-replacement', name='Replacement'
        )
        self._write_catalog()

        report = import_catalog(catalog_path=self.catalog_file.name)
        activity.refresh_from_db()
        self.assertEqual(report.games.orphaned, 1)
        self.assertTrue(activity.active)
        report = import_catalog(catalog_path=self.catalog_file.name, deactivate_missing=True)
        activity.refresh_from_db(); game.refresh_from_db()
        self.assertEqual(report.games.deactivated, 1)
        self.assertFalse(activity.active)
        self.assertFalse(game.active)

    def test_structural_error_does_not_write_and_command_has_human_and_json_reports(self):
        bad = deepcopy(self.catalog)
        bad['generations'][0]['platforms'][0]['games'][0]['estimated_main_minutes'] = 0
        self.catalog = bad
        self._write_catalog()
        baseline_categories = Category.objects.count()
        with self.assertRaises(CatalogValidationError):
            import_catalog(catalog_path=self.catalog_file.name)
        self.assertEqual(Category.objects.count(), baseline_categories)

        self.catalog = minimal_catalog(); self._write_catalog()
        human = StringIO()
        call_command('import_retrogames_catalog', '--dry-run', '--catalog-file', self.catalog_file.name, stdout=human)
        self.assertIn('Gerações: criados=1', human.getvalue())
<<<<<<< HEAD
        self.assertIn('Detalhes:', human.getvalue())
=======
>>>>>>> 8d67d31e92eef7ef8d9dd2c3ef968ca73eddccb4
        output = StringIO()
        call_command('import_retrogames_catalog', '--dry-run', '--format=json', '--catalog-file', self.catalog_file.name, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['games']['created'], 1)

    def test_command_returns_error_for_invalid_catalog(self):
        self.catalog['schema_version'] = 99
        self._write_catalog()
        with self.assertRaises(CommandError):
            call_command('import_retrogames_catalog', '--dry-run', '--catalog-file', self.catalog_file.name)

    def test_database_failure_rolls_back_the_complete_import(self):
        baseline = (
            Group.objects.count(), Category.objects.count(), Activity.objects.count(),
            RetroPlatform.objects.count(), RetroGame.objects.count(),
        )
        with patch.object(RetroGame, 'save', side_effect=RuntimeError('falha simulada')):
            with self.assertRaisesRegex(RuntimeError, 'falha simulada'):
                import_catalog(catalog_path=self.catalog_file.name)
        self.assertEqual(
            baseline,
            (
                Group.objects.count(), Category.objects.count(), Activity.objects.count(),
                RetroPlatform.objects.count(), RetroGame.objects.count(),
            ),
        )
