from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from json import JSONDecodeError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from apps.pomodoro.models import Activity, Category
from apps.pomodoro.services.activity_queue_reconciliation import (
    activity_snapshot,
    reconcile_activity,
)


STEAM_API_URL = 'https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/'
STEAM_EXTERNAL_SOURCE = 'steam'


class SteamImportError(Exception):
    """Erro seguro para apresentação na interface administrativa."""


@dataclass(frozen=True)
class SteamImportConfig:
    api_key: str
    steam_id: str
    category_id: int
    default_duration: int
    timeout: int


@dataclass(frozen=True)
class SteamImportResult:
    total: int
    created: int
    updated: int
    skipped: int
    errors: int


def _positive_integer(value: object, *, setting_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SteamImportError(f'A configuração {setting_name} deve ser um inteiro positivo.') from exc
    if parsed <= 0:
        raise SteamImportError(f'A configuração {setting_name} deve ser um inteiro positivo.')
    return parsed


def get_steam_import_config() -> SteamImportConfig:
    api_key = str(getattr(settings, 'STEAM_API_KEY', '')).strip()
    if not api_key:
        raise SteamImportError('STEAM_API_KEY não está configurada no servidor.')

    steam_id = str(getattr(settings, 'STEAM_ID64', '')).strip()
    if not steam_id.isdigit() or int(steam_id) <= 0:
        raise SteamImportError('A configuração STEAM_ID64 deve ser um identificador numérico válido.')

    return SteamImportConfig(
        api_key=api_key,
        steam_id=steam_id,
        category_id=_positive_integer(
            getattr(settings, 'STEAM_ACTIVITY_CATEGORY_ID', 21),
            setting_name='STEAM_ACTIVITY_CATEGORY_ID',
        ),
        default_duration=_positive_integer(
            getattr(settings, 'STEAM_ACTIVITY_DEFAULT_DURATION', 60),
            setting_name='STEAM_ACTIVITY_DEFAULT_DURATION',
        ),
        timeout=_positive_integer(
            getattr(settings, 'STEAM_API_TIMEOUT_SECONDS', 10),
            setting_name='STEAM_API_TIMEOUT_SECONDS',
        ),
    )


def fetch_owned_games(*, api_key: str, steam_id: str, timeout: int) -> list[dict[str, object]]:
    query = urlencode(
        {
            'key': api_key,
            'steamid': steam_id,
            'include_appinfo': 'true',
            'include_played_free_games': 'true',
            'include_free_sub': 'true',
            'skip_unvetted_apps': 'false',
            'format': 'json',
        }
    )
    request = Request(
        f'{STEAM_API_URL}?{query}',
        headers={'Accept': 'application/json', 'User-Agent': 'PomodoroTask/SteamImporter'},
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, 'status', response.getcode())
            payload = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise SteamImportError('A Steam recusou as credenciais configuradas.') from None
        raise SteamImportError(f'A Steam respondeu com HTTP {exc.code}.') from None
    except (TimeoutError, socket.timeout):
        raise SteamImportError('A consulta à Steam excedeu o tempo limite.') from None
    except (URLError, OSError):
        raise SteamImportError('A Steam está indisponível no momento.') from None

    if status in (401, 403):
        raise SteamImportError('A Steam recusou as credenciais configuradas.')
    if not 200 <= status < 300:
        raise SteamImportError(f'A Steam respondeu com HTTP {status}.')

    try:
        data = json.loads(payload.decode('utf-8'))
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise SteamImportError('A Steam retornou uma resposta JSON inválida.') from exc

    if not isinstance(data, dict) or not isinstance(data.get('response'), dict):
        raise SteamImportError('A Steam retornou uma resposta em formato inesperado.')

    games = data['response'].get('games', [])
    if games is None:
        games = []
    if not isinstance(games, list):
        raise SteamImportError('A Steam retornou uma lista de jogos inválida.')
    if any(not isinstance(game, dict) for game in games):
        raise SteamImportError('A Steam retornou itens de jogos inválidos.')
    return games


def _normalized_game(game: dict[str, object]) -> tuple[str, str]:
    appid = game.get('appid')
    if isinstance(appid, bool):
        raise ValueError('appid inválido')
    try:
        parsed_appid = int(appid)
    except (TypeError, ValueError) as exc:
        raise ValueError('appid inválido') from exc
    if parsed_appid <= 0:
        raise ValueError('appid inválido')

    raw_name = game.get('name')
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError('nome inválido')
    name = raw_name.strip()
    if len(name) > Activity._meta.get_field('name').max_length:
        raise ValueError('nome excede o limite do model')
    return str(parsed_appid), name


@transaction.atomic
def _persist_game(
    *,
    appid: str,
    name: str,
    category: Category,
    default_duration: int,
) -> str:
    description = (
        'Jogo importado automaticamente da biblioteca Steam. '
        f'Steam AppID: {appid}.'
    )
    activity = (
        Activity.objects.select_for_update()
        .filter(external_source=STEAM_EXTERNAL_SOURCE, external_id=appid)
        .first()
    )

    if activity is None:
        activity = Activity.objects.create(
            name=name,
            description=description,
            category=category,
            duration=default_duration,
            active=True,
            premium=False,
            executions_today=0,
            priority=1,
            external_source=STEAM_EXTERNAL_SOURCE,
            external_id=appid,
        )
        reconcile_activity(activity)
        return 'created'

    previous = activity_snapshot(activity)
    changed_fields = []
    controlled_values = {
        'name': name,
        'description': description,
        'category_id': category.id,
    }
    for field_name, expected_value in controlled_values.items():
        if getattr(activity, field_name) != expected_value:
            setattr(activity, field_name, expected_value)
            changed_fields.append(field_name)

    if not changed_fields:
        return 'skipped'

    activity.save(update_fields=changed_fields)
    if 'category_id' in changed_fields:
        reconcile_activity(activity, previous=previous)
    return 'updated'


def import_steam_games() -> SteamImportResult:
    config = get_steam_import_config()
    try:
        category = Category.objects.get(pk=config.category_id)
    except Category.DoesNotExist as exc:
        raise SteamImportError(
            f'A categoria de ID {config.category_id} não existe. '
            'Cadastre ou configure uma categoria válida antes de importar.'
        ) from exc

    games = fetch_owned_games(
        api_key=config.api_key,
        steam_id=config.steam_id,
        timeout=config.timeout,
    )
    counters = {'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}

    for game in games:
        try:
            appid, name = _normalized_game(game)
            outcome = _persist_game(
                appid=appid,
                name=name,
                category=category,
                default_duration=config.default_duration,
            )
        except Exception:
            # Cada item possui transação própria; falhas não deixam atividade
            # persistida sem a reconciliação correspondente e não abortam o lote.
            counters['errors'] += 1
            continue
        counters[outcome] += 1

    return SteamImportResult(total=len(games), **counters)
