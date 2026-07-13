import json
import socket
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.pomodoro.models import Activity, Category, Group
from apps.pomodoro.services.steam_import import (
    SteamImportError,
    SteamImportResult,
    fetch_owned_games,
    import_steam_games,
)


class FakeHTTPResponse:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return self.payload

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


@override_settings(
    STEAM_API_KEY='test-steam-key',
    STEAM_ID64='76561198065747727',
    STEAM_ACTIVITY_CATEGORY_ID='21',
    STEAM_ACTIVITY_DEFAULT_DURATION='60',
    STEAM_API_TIMEOUT_SECONDS=3,
)
class SteamImportServiceTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='Jogos', is_default=True)
        self.category = Category.objects.create(
            pk=21,
            name='Steam',
            group=self.group,
            max_daily_executions=100,
        )

    @patch('apps.pomodoro.services.steam_import.reconcile_activity')
    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_imports_new_games_with_expected_mapping_and_reconciliation(self, fetch, reconcile):
        fetch.return_value = [
            {'appid': 10, 'name': 'Counter-Strike'},
            {'appid': '20', 'name': 'Team Fortress Classic'},
        ]

        result = import_steam_games()

        self.assertEqual(result, SteamImportResult(2, 2, 0, 0, 0))
        activities = Activity.objects.order_by('external_id')
        self.assertEqual(list(activities.values_list('external_id', flat=True)), ['10', '20'])
        first = activities[0]
        self.assertEqual(first.external_source, 'steam')
        self.assertEqual(first.category_id, 21)
        self.assertEqual(first.duration, 60)
        self.assertTrue(first.active)
        self.assertFalse(first.premium)
        self.assertIsNone(first.premium_from)
        self.assertIsNone(first.premium_until)
        self.assertEqual(first.executions_today, 0)
        self.assertEqual(first.priority, 1)
        self.assertEqual(
            first.description,
            'Jogo importado automaticamente da biblioteca Steam. Steam AppID: 10.',
        )
        self.assertEqual(reconcile.call_count, 2)

    @patch('apps.pomodoro.services.steam_import.reconcile_activity')
    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_second_import_is_idempotent(self, fetch, reconcile):
        fetch.return_value = [{'appid': 10, 'name': 'Counter-Strike'}]

        first = import_steam_games()
        second = import_steam_games()

        self.assertEqual(first.created, 1)
        self.assertEqual(second, SteamImportResult(1, 0, 0, 1, 0))
        self.assertEqual(Activity.objects.filter(external_source='steam', external_id='10').count(), 1)
        self.assertEqual(reconcile.call_count, 1)

    @patch('apps.pomodoro.services.steam_import.reconcile_activity')
    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_sync_updates_controlled_fields_and_preserves_manual_fields(self, fetch, reconcile):
        fetch.return_value = [{'appid': 10, 'name': 'Nome antigo'}]
        import_steam_games()
        activity = Activity.objects.get(external_id='10')
        other_category = Category.objects.create(name='Manual', group=self.group)
        activity.name = 'Edição local'
        activity.description = 'Descrição local'
        activity.category = other_category
        activity.duration = 25
        activity.priority = 9
        activity.active = False
        activity.executions_today = 7
        activity.save()
        fetch.return_value = [{'appid': 10, 'name': 'Nome novo'}]
        reconcile.reset_mock()

        result = import_steam_games()

        activity.refresh_from_db()
        self.assertEqual(result, SteamImportResult(1, 0, 1, 0, 0))
        self.assertEqual(activity.name, 'Nome novo')
        self.assertEqual(activity.category_id, 21)
        self.assertEqual(activity.duration, 25)
        self.assertEqual(activity.priority, 9)
        self.assertFalse(activity.active)
        self.assertEqual(activity.executions_today, 7)
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs['previous']['category_id'], other_category.id)

    @override_settings(STEAM_ACTIVITY_CATEGORY_ID='999')
    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_missing_category_aborts_before_http_request(self, fetch):
        with self.assertRaisesRegex(SteamImportError, 'categoria de ID 999 não existe'):
            import_steam_games()

        fetch.assert_not_called()
        self.assertFalse(Activity.objects.exists())

    @override_settings(STEAM_API_KEY='')
    def test_missing_api_key_is_rejected(self):
        with self.assertRaisesRegex(SteamImportError, 'STEAM_API_KEY'):
            import_steam_games()

    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_invalid_items_are_counted_as_errors(self, fetch):
        fetch.return_value = [
            {'name': 'Sem AppID'},
            {'appid': 'inválido', 'name': 'AppID inválido'},
            {'appid': 30},
            {'appid': 40, 'name': '   '},
        ]

        result = import_steam_games()

        self.assertEqual(result, SteamImportResult(4, 0, 0, 0, 4))
        self.assertFalse(Activity.objects.exists())

    @patch('apps.pomodoro.services.steam_import.reconcile_activity', side_effect=RuntimeError)
    @patch('apps.pomodoro.services.steam_import.fetch_owned_games')
    def test_reconciliation_failure_rolls_back_only_failed_game(self, fetch, _reconcile):
        fetch.return_value = [{'appid': 10, 'name': 'Counter-Strike'}]

        result = import_steam_games()

        self.assertEqual(result.errors, 1)
        self.assertFalse(Activity.objects.filter(external_id='10').exists())

    def test_external_identity_constraint_prevents_duplicates(self):
        Activity.objects.create(
            name='Primeiro',
            category=self.category,
            external_source='steam',
            external_id='10',
        )

        with self.assertRaises(IntegrityError):
            Activity.objects.create(
                name='Duplicado',
                category=self.category,
                external_source='steam',
                external_id='10',
            )


class SteamHTTPClientTests(TestCase):
    @patch('apps.pomodoro.services.steam_import.urlopen')
    def test_fetches_games_with_all_expected_parameters(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(
            json.dumps({'response': {'games': [{'appid': 10, 'name': 'CS'}]}}).encode()
        )

        games = fetch_owned_games(api_key='safe-test-key', steam_id='123', timeout=4)

        self.assertEqual(games, [{'appid': 10, 'name': 'CS'}])
        request = urlopen.call_args.args[0]
        self.assertIn('include_appinfo=true', request.full_url)
        self.assertIn('include_played_free_games=true', request.full_url)
        self.assertIn('include_free_sub=true', request.full_url)
        self.assertIn('skip_unvetted_apps=false', request.full_url)
        self.assertIn('format=json', request.full_url)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 4)

    @patch('apps.pomodoro.services.steam_import.urlopen')
    def test_http_error_is_sanitized(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(b'{}', status=503)

        with self.assertRaisesRegex(SteamImportError, 'HTTP 503') as raised:
            fetch_owned_games(api_key='secret-that-must-not-leak', steam_id='123', timeout=4)

        self.assertNotIn('secret-that-must-not-leak', str(raised.exception))

    @patch('apps.pomodoro.services.steam_import.urlopen', side_effect=socket.timeout)
    def test_timeout_is_sanitized(self, _urlopen):
        with self.assertRaisesRegex(SteamImportError, 'tempo limite'):
            fetch_owned_games(api_key='secret-that-must-not-leak', steam_id='123', timeout=4)

    @patch('apps.pomodoro.services.steam_import.urlopen')
    def test_invalid_json_is_rejected(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(b'not-json')

        with self.assertRaisesRegex(SteamImportError, 'JSON inválida'):
            fetch_owned_games(api_key='test-key', steam_id='123', timeout=4)

    @patch('apps.pomodoro.services.steam_import.urlopen')
    def test_valid_response_without_games_returns_empty_list(self, urlopen):
        urlopen.return_value = FakeHTTPResponse(b'{"response": {"game_count": 0}}')

        games = fetch_owned_games(api_key='test-key', steam_id='123', timeout=4)

        self.assertEqual(games, [])


@override_settings(
    STEAM_API_KEY='test-steam-key',
    STEAM_ID64='76561198065747727',
    STEAM_ACTIVITY_CATEGORY_ID='21',
    STEAM_ACTIVITY_DEFAULT_DURATION='60',
)
class SteamImportAdminTests(TestCase):
    def setUp(self):
        self.url = reverse('admin:pomodoro_activity_import_steam')
        self.changelist_url = reverse('admin:pomodoro_activity_changelist')
        self.superuser = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='test-password',
        )

    @patch('apps.pomodoro.admin.import_steam_games')
    def test_endpoint_rejects_get_without_executing_import(self, import_games):
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        import_games.assert_not_called()

    def test_changelist_contains_post_form_and_csrf_token(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self.changelist_url)

        self.assertContains(response, 'Importar jogos da Steam')
        self.assertContains(response, f'action="{self.url}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')

    @patch('apps.pomodoro.admin.import_steam_games')
    def test_endpoint_accepts_post_for_authorized_admin(self, import_games):
        import_games.return_value = SteamImportResult(227, 220, 5, 2, 0)
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, follow=True)

        self.assertRedirects(response, self.changelist_url)
        import_games.assert_called_once_with()
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            'Steam: 227 jogos encontrados; 220 criados; 5 atualizados; 2 ignorados; 0 erros.',
            messages,
        )

    @patch('apps.pomodoro.admin.import_steam_games')
    def test_endpoint_requires_csrf_token(self, import_games):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.superuser)

        response = csrf_client.post(self.url)

        self.assertEqual(response.status_code, 403)
        import_games.assert_not_called()

    @patch('apps.pomodoro.admin.import_steam_games')
    def test_endpoint_rejects_admin_without_change_permission(self, import_games):
        user = get_user_model().objects.create_user(
            username='limited-admin',
            password='test-password',
            is_staff=True,
        )
        user.user_permissions.add(Permission.objects.get(codename='add_activity'))
        self.client.force_login(user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        import_games.assert_not_called()

    @patch('apps.pomodoro.admin.import_steam_games')
    def test_admin_error_message_does_not_expose_api_key(self, import_games):
        import_games.side_effect = SteamImportError('A Steam recusou as credenciais configuradas.')
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, follow=True)

        content = response.content.decode()
        self.assertNotIn('test-steam-key', content)
        self.assertContains(response, 'A Steam recusou as credenciais configuradas.')
