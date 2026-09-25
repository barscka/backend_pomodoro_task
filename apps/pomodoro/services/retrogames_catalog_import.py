from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.pomodoro.models import Activity, Category, Group, RetroGame, RetroPlatform


CATALOG_SOURCE = 'retrogames_catalog'
SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / 'data' / 'retrogames_catalog.json'
TIERS = {RetroGame.TIER_ESSENTIAL, RetroGame.TIER_COMPLEMENTARY}
TECHNICAL_PARTS = {'bios', 'firmware', 'saves', 'save', 'states', 'cache', 'caches', 'config'}
TECHNICAL_EXTENSIONS = {
    '.cfg', '.conf', '.dat', '.db', '.ini', '.json', '.log', '.sav', '.srm', '.state', '.txt', '.xml'
}
GAME_EXTENSIONS = {
    '.3ds', '.7z', '.a26', '.bin', '.cdi', '.chd', '.cia', '.cso', '.cue', '.gb', '.gba', '.gbc',
    '.gen', '.gg', '.iso', '.md', '.n64', '.nds', '.nes', '.ngc', '.nsp', '.pce', '.pbp',
    '.rom', '.sfc', '.smc', '.sms', '.v64', '.ws', '.wsc', '.xci', '.z64', '.zip',
}


class CatalogImportError(Exception):
    pass


class CatalogValidationError(CatalogImportError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__('Catálogo inválido:\n- ' + '\n- '.join(errors))


@dataclass
class EntityStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    orphaned: int = 0


@dataclass
class ImportReport:
    schema_version: int
    catalog_version: str
    dry_run: bool
    group: EntityStats = field(default_factory=EntityStats)
    generations: EntityStats = field(default_factory=EntityStats)
    platforms: EntityStats = field(default_factory=EntityStats)
    games: EntityStats = field(default_factory=EntityStats)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    inventory: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        def stats(value: EntityStats) -> dict[str, int]:
            return {
                'created': value.created,
                'updated': value.updated,
                'unchanged': value.unchanged,
                'deactivated': value.deactivated,
                'orphaned': value.orphaned,
            }

        return {
            'schema_version': self.schema_version,
            'catalog_version': self.catalog_version,
            'dry_run': self.dry_run,
            'group': stats(self.group),
            'generations': stats(self.generations),
            'platforms': stats(self.platforms),
            'games': stats(self.games),
            'inventory': self.inventory,
            'changes': self.changes,
            'warnings': self.warnings,
        }


def load_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        raise CatalogImportError(f'Não foi possível ler o catálogo {path}: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise CatalogImportError(
            f'JSON inválido em {path}:{exc.lineno}:{exc.colno}: {exc.msg}'
        ) from exc


def _required(mapping: Any, key: str, path: str, errors: list[str]) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        errors.append(f'{path}.{key}: campo obrigatório ausente')
        return None
    return mapping[key]


def _non_empty(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f'{path}: deve ser texto não vazio')


def _integer(value: Any, path: str, errors: list[str], *, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        errors.append(f'{path}: deve ser inteiro maior ou igual a {minimum}')
    elif maximum is not None and value > maximum:
        errors.append(f'{path}: deve ser menor ou igual a {maximum}')


def validate_catalog(catalog: Any) -> None:
    errors: list[str] = []
    if not isinstance(catalog, dict):
        raise CatalogValidationError(['$: deve ser um objeto JSON'])

    schema_version = _required(catalog, 'schema_version', '$', errors)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            f'$.schema_version: versão {schema_version!r} não suportada; esperada {SUPPORTED_SCHEMA_VERSION}'
        )
    _non_empty(_required(catalog, 'catalog_version', '$', errors), '$.catalog_version', errors)
    group = _required(catalog, 'group', '$', errors)
    _non_empty(_required(group, 'name', '$.group', errors), '$.group.name', errors)

    generations = _required(catalog, 'generations', '$', errors)
    if not isinstance(generations, list) or not generations:
        errors.append('$.generations: deve conter ao menos uma geração')
        generations = []

    generation_keys: set[str] = set()
    generation_orders: set[int] = set()
    platform_slugs: set[str] = set()
    game_keys: set[str] = set()
    expected_total = 0
    for gi, generation in enumerate(generations):
        gp = f'generations[{gi}]'
        key = _required(generation, 'key', gp, errors)
        _non_empty(key, f'{gp}.key', errors)
        if isinstance(key, str) and key in generation_keys:
            errors.append(f'{gp}.key: chave duplicada {key!r}')
        generation_keys.add(key)
        _non_empty(_required(generation, 'name', gp, errors), f'{gp}.name', errors)
        order = _required(generation, 'sort_order', gp, errors)
        _integer(order, f'{gp}.sort_order', errors, minimum=1)
        if isinstance(order, int) and order in generation_orders:
            errors.append(f'{gp}.sort_order: ordem duplicada {order}')
        generation_orders.add(order)
        platforms = _required(generation, 'platforms', gp, errors)
        if not isinstance(platforms, list) or not platforms:
            errors.append(f'{gp}.platforms: deve conter ao menos uma plataforma')
            continue
        platform_orders: set[int] = set()
        for pi, platform in enumerate(platforms):
            pp = f'{gp}.platforms[{pi}]'
            slug = _required(platform, 'slug', pp, errors)
            _non_empty(slug, f'{pp}.slug', errors)
            if isinstance(slug, str) and (not re.fullmatch(r'[-a-z0-9]+', slug)):
                errors.append(f'{pp}.slug: formato inválido')
            if isinstance(slug, str) and slug in platform_slugs:
                errors.append(f'{pp}.slug: slug duplicado {slug!r}')
            platform_slugs.add(slug)
            _non_empty(_required(platform, 'name', pp, errors), f'{pp}.name', errors)
            porder = _required(platform, 'sort_order', pp, errors)
            _integer(porder, f'{pp}.sort_order', errors, minimum=1)
            if isinstance(porder, int) and porder in platform_orders:
                errors.append(f'{pp}.sort_order: ordem duplicada {porder}')
            platform_orders.add(porder)
            release_year = platform.get('release_year')
            if release_year is not None:
                _integer(release_year, f'{pp}.release_year', errors, minimum=1970, maximum=2100)
            games = _required(platform, 'games', pp, errors)
            if not isinstance(games, list) or not games:
                errors.append(f'{pp}.games: deve conter ao menos um jogo')
                continue
            game_orders: set[int] = set()
            for ji, game in enumerate(games):
                jp = f'{pp}.games[{ji}]'
                game_key = _required(game, 'key', jp, errors)
                _non_empty(game_key, f'{jp}.key', errors)
                if isinstance(game_key, str) and len(game_key) > 50:
                    errors.append(f'{jp}.key: deve possuir no máximo 50 caracteres')
                if isinstance(game_key, str) and game_key in game_keys:
                    errors.append(f'{jp}.key: chave duplicada {game_key!r}')
                game_keys.add(game_key)
                _non_empty(_required(game, 'name', jp, errors), f'{jp}.name', errors)
                tier = _required(game, 'tier', jp, errors)
                if tier not in TIERS:
                    errors.append(f'{jp}.tier: deve ser essential ou complementary')
                minutes = _required(game, 'estimated_main_minutes', jp, errors)
                _integer(minutes, f'{jp}.estimated_main_minutes', errors, minimum=1)
                if isinstance(minutes, int) and not isinstance(minutes, bool):
                    expected_total += minutes
                duration = _required(game, 'default_block_minutes', jp, errors)
                _integer(duration, f'{jp}.default_block_minutes', errors, minimum=1, maximum=720)
                game_order = _required(game, 'sort_order', jp, errors)
                _integer(game_order, f'{jp}.sort_order', errors, minimum=1)
                if isinstance(game_order, int) and game_order in game_orders:
                    errors.append(f'{jp}.sort_order: ordem duplicada {game_order}')
                game_orders.add(game_order)
                release_year = game.get('release_year')
                if release_year is not None:
                    _integer(release_year, f'{jp}.release_year', errors, minimum=1970, maximum=2100)
                aliases = game.get('aliases', [])
                if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
                    errors.append(f'{jp}.aliases: deve ser uma lista de textos não vazios')
                if not isinstance(game.get('active', True), bool):
                    errors.append(f'{jp}.active: deve ser booleano')

    declared_total = catalog.get('total_estimated_minutes')
    if declared_total is not None:
        _integer(declared_total, '$.total_estimated_minutes', errors, minimum=1)
        if isinstance(declared_total, int) and declared_total != expected_total:
            errors.append(
                '$.total_estimated_minutes: '
                f'declarado {declared_total}, mas a soma dos jogos é {expected_total}'
            )
    source_total = catalog.get('source_declared_total_minutes')
    if source_total is not None:
        _integer(source_total, '$.source_declared_total_minutes', errors, minimum=1)
    if errors:
        raise CatalogValidationError(errors)


def iter_catalog(catalog: dict[str, Any]):
    for generation in catalog['generations']:
        for platform in generation['platforms']:
            for game in platform['games']:
                yield generation, platform, game


def _changed(instance: Any, values: dict[str, Any]) -> list[str]:
    return [name for name, value in values.items() if getattr(instance, name) != value]


def _record(stats: EntityStats, exists: bool, changed: list[str]) -> None:
    if not exists:
        stats.created += 1
    elif changed:
        stats.updated += 1
    else:
        stats.unchanged += 1


def _desired_group(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        'name': catalog['group']['name'],
        'description': catalog['group'].get('description', ''),
        'color': catalog['group'].get('color', '#6C5CE7'),
        'is_retro_catalog': True,
    }


def _desired_generation(generation: dict[str, Any], group_id: int | None) -> dict[str, Any]:
    return {
        'name': generation['name'],
        'description': generation.get('description', ''),
        'color': generation.get('color', '#6C5CE7'),
        'group_id': group_id,
        'retro_sort_order': generation['sort_order'],
    }


def _desired_platform(platform: dict[str, Any], generation_id: int | None) -> dict[str, Any]:
    return {
        'generation_id': generation_id,
        'name': platform['name'],
        'manufacturer': platform.get('manufacturer', ''),
        'release_year': platform.get('release_year'),
        'sort_order': platform['sort_order'],
        'active': platform.get('active', True),
    }


def _desired_activity(game: dict[str, Any], category_id: int | None) -> dict[str, Any]:
    return {
        'name': game['name'],
        'description': game.get('description', ''),
        'duration': game['default_block_minutes'],
        'category_id': category_id,
        'external_source': CATALOG_SOURCE,
        'external_id': game['key'],
        'active': game.get('active', True),
    }


def _desired_game(game: dict[str, Any], activity_id: int | None, platform_id: int | None) -> dict[str, Any]:
    return {
        'activity_id': activity_id,
        'platform_id': platform_id,
        'tier': game['tier'],
        'estimated_main_minutes': game['estimated_main_minutes'],
        'play_goal': game.get('play_goal', ''),
        'release_year': game.get('release_year'),
        'sort_order': game['sort_order'],
        'active': game.get('active', True),
        'cover_url': game.get('cover_url'),
    }


def _plan(catalog: dict[str, Any], *, deactivate_missing: bool) -> ImportReport:
    report = ImportReport(catalog['schema_version'], catalog['catalog_version'], True)
    group = Group.objects.filter(is_retro_catalog=True).first()
    group_changed = _changed(group, _desired_group(catalog)) if group else []
    _record(report.group, group is not None, group_changed)
    if group_changed:
        report.changes.append(f"Grupo {group.name!r}: atualizar {', '.join(group_changed)}")

    generations = {item.name: item for item in Category.objects.filter(group=group)} if group else {}
    platforms = {item.slug: item for item in RetroPlatform.objects.select_related('generation')}
    activities = {
        item.external_id: item
        for item in Activity.objects.filter(external_source=CATALOG_SOURCE)
    }
    retro_games = {
        item.activity_id: item
        for item in RetroGame.objects.select_related('activity', 'platform')
        .filter(activity__external_source=CATALOG_SOURCE)
    }
    catalog_keys: set[str] = set()
    catalog_slugs: set[str] = set()
    for generation in catalog['generations']:
        generation_obj = generations.get(generation['name'])
        changed = _changed(generation_obj, _desired_generation(generation, group.id)) if generation_obj and group else []
        _record(report.generations, generation_obj is not None, changed)
        if changed:
            report.changes.append(
                f"Geração {generation['key']!r}: atualizar {', '.join(changed)}"
            )
        for platform in generation['platforms']:
            catalog_slugs.add(platform['slug'])
            platform_obj = platforms.get(platform['slug'])
            desired_generation_id = generation_obj.id if generation_obj else None
            changed = _changed(platform_obj, _desired_platform(platform, desired_generation_id)) if platform_obj else []
            _record(report.platforms, platform_obj is not None, changed)
            if changed:
                report.changes.append(
                    f"Plataforma {platform['slug']!r}: atualizar {', '.join(changed)}"
                )
            for game in platform['games']:
                catalog_keys.add(game['key'])
                activity = activities.get(game['key'])
                activity_changed = _changed(activity, _desired_activity(game, desired_generation_id)) if activity else []
                retro_game = retro_games.get(activity.id) if activity else None
                desired_platform_id = platform_obj.id if platform_obj else None
                game_changed = _changed(retro_game, _desired_game(game, activity.id, desired_platform_id)) if retro_game else []
                exists = activity is not None and retro_game is not None
                _record(report.games, exists, sorted(set(activity_changed + game_changed)))
                if activity_changed or game_changed:
                    fields = [f'activity.{field}' for field in activity_changed]
                    fields.extend(f'retro_game.{field}' for field in game_changed)
                    report.changes.append(
                        f"Jogo {game['key']!r}: atualizar {', '.join(fields)}"
                    )

    orphan_activities = [activity for key, activity in activities.items() if key not in catalog_keys]
    report.games.orphaned = len(orphan_activities)
    report.changes.extend(
        f"Jogo {activity.external_id!r}: órfão; "
        + ('desativar' if deactivate_missing else 'manter ativo')
        for activity in orphan_activities
    )
    if deactivate_missing:
        report.games.deactivated = sum(
            activity.active or (hasattr(activity, 'retro_game') and activity.retro_game.active)
            for activity in orphan_activities
        )
    for platform in platforms.values():
        if group and platform.generation.group_id == group.id and platform.slug not in catalog_slugs:
            report.platforms.orphaned += 1
            report.warnings.append(
                f'Plataforma órfã {platform.slug!r} mantida: o modelo não registra proveniência do catálogo.'
            )
    return report


def _save_if_changed(instance: Any, values: dict[str, Any]) -> list[str]:
    fields = _changed(instance, values)
    if fields:
        for name in fields:
            setattr(instance, name, values[name])
        if isinstance(instance, (RetroPlatform, RetroGame)):
            instance.full_clean()
        instance.save(update_fields=fields)
    return fields


def _apply(catalog: dict[str, Any], *, deactivate_missing: bool) -> ImportReport:
    report = ImportReport(catalog['schema_version'], catalog['catalog_version'], False)
    with transaction.atomic():
        locked_group = Group.objects.select_for_update().filter(is_retro_catalog=True).first()
        if locked_group is None:
            conflicting_name = Group.objects.select_for_update().filter(name=catalog['group']['name']).first()
            if conflicting_name and not conflicting_name.is_retro_catalog:
                locked_group = conflicting_name
                changed = _save_if_changed(locked_group, _desired_group(catalog))
                _record(report.group, True, changed)
                report.changes.append(
                    f"Grupo {locked_group.name!r}: atualizar {', '.join(changed)}"
                )
            else:
                locked_group = Group.objects.create(**_desired_group(catalog))
                report.group.created += 1
        else:
            changed = _save_if_changed(locked_group, _desired_group(catalog))
            _record(report.group, True, changed)
            if changed:
                report.changes.append(
                    f"Grupo {locked_group.name!r}: atualizar {', '.join(changed)}"
                )

        catalog_keys: set[str] = set()
        catalog_slugs: set[str] = set()
        for generation in catalog['generations']:
            generation_obj = Category.objects.select_for_update().filter(
                group=locked_group, name=generation['name']
            ).first()
            if generation_obj is None:
                generation_obj = Category.objects.create(**_desired_generation(generation, locked_group.id))
                report.generations.created += 1
            else:
                changed = _save_if_changed(generation_obj, _desired_generation(generation, locked_group.id))
                _record(report.generations, True, changed)
                if changed:
                    report.changes.append(
                        f"Geração {generation['key']!r}: atualizar {', '.join(changed)}"
                    )

            for platform in generation['platforms']:
                catalog_slugs.add(platform['slug'])
                platform_obj = RetroPlatform.objects.select_for_update().filter(slug=platform['slug']).first()
                if platform_obj is None:
                    platform_obj = RetroPlatform(slug=platform['slug'], **_desired_platform(platform, generation_obj.id))
                    platform_obj.full_clean()
                    platform_obj.save()
                    report.platforms.created += 1
                else:
                    changed = _save_if_changed(platform_obj, _desired_platform(platform, generation_obj.id))
                    _record(report.platforms, True, changed)
                    if changed:
                        report.changes.append(
                            f"Plataforma {platform['slug']!r}: atualizar {', '.join(changed)}"
                        )

                for game in platform['games']:
                    catalog_keys.add(game['key'])
                    activity = Activity.objects.select_for_update().filter(
                        external_source=CATALOG_SOURCE, external_id=game['key']
                    ).first()
                    activity_created = activity is None
                    if activity_created:
                        activity = Activity(**_desired_activity(game, generation_obj.id))
                        activity.save()
                        activity_changed = ['created']
                    else:
                        activity_changed = _save_if_changed(activity, _desired_activity(game, generation_obj.id))

                    retro_game = RetroGame.objects.select_for_update().filter(activity=activity).first()
                    retro_created = retro_game is None
                    if retro_created:
                        retro_game = RetroGame(**_desired_game(game, activity.id, platform_obj.id))
                        retro_game.full_clean()
                        retro_game.save()
                        retro_changed = ['created']
                    else:
                        retro_changed = _save_if_changed(
                            retro_game, _desired_game(game, activity.id, platform_obj.id)
                        )
                    _record(
                        report.games,
                        not (activity_created or retro_created),
                        sorted(set(activity_changed + retro_changed)),
                    )
                    if not (activity_created or retro_created) and (activity_changed or retro_changed):
                        fields = [f'activity.{field}' for field in activity_changed]
                        fields.extend(f'retro_game.{field}' for field in retro_changed)
                        report.changes.append(
                            f"Jogo {game['key']!r}: atualizar {', '.join(fields)}"
                        )

        orphan_activities = list(
            Activity.objects.select_for_update()
            .filter(external_source=CATALOG_SOURCE)
            .exclude(external_id__in=catalog_keys)
        )
        report.games.orphaned = len(orphan_activities)
        report.changes.extend(
            f"Jogo {activity.external_id!r}: órfão; "
            + ('desativar' if deactivate_missing else 'manter ativo')
            for activity in orphan_activities
        )
        if deactivate_missing:
            for activity in orphan_activities:
                changed = False
                if activity.active:
                    activity.active = False
                    activity.save(update_fields=['active'])
                    changed = True
                retro_game = RetroGame.objects.select_for_update().filter(activity=activity).first()
                if retro_game and retro_game.active:
                    retro_game.active = False
                    retro_game.save(update_fields=['active'])
                    changed = True
                report.games.deactivated += int(changed)

        for platform in RetroPlatform.objects.select_related('generation').filter(
            generation__group=locked_group
        ).exclude(slug__in=catalog_slugs):
            report.platforms.orphaned += 1
            report.warnings.append(
                f'Plataforma órfã {platform.slug!r} mantida: o modelo não registra proveniência do catálogo.'
            )
    return report


def _normalize_title(value: str) -> str:
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r'\([^)]*\)|\[[^]]*\]', ' ', value)
    value = re.sub(r'\b(?:usa|europe|japan|world|rev|en|pt|brasil|brazil)\b', ' ', value, flags=re.I)
    value = re.sub(r'[^a-z0-9]+', ' ', value.casefold())
    value = re.sub(r'^\s*\d+\s+', '', value)
    return ''.join(value.split())


def _inventory_titles(path: Path | str) -> set[str]:
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        raise CatalogImportError(f'Não foi possível ler o inventário {path}: {exc}') from exc
    titles: set[str] = set()
    for raw in lines:
        item = Path(raw.strip())
        parts = {part.casefold() for part in item.parts}
        if parts & TECHNICAL_PARTS or item.suffix.casefold() in TECHNICAL_EXTENSIONS:
            continue
        title = item.name
        while Path(title).suffix.casefold() in GAME_EXTENSIONS:
            title = Path(title).stem
        normalized = _normalize_title(title)
        if normalized:
            titles.add(normalized)
    return titles


def compare_inventory(catalog: dict[str, Any], inventory_path: Path | str) -> tuple[dict[str, int], list[str]]:
    titles = _inventory_titles(inventory_path)
    counts = {'confirmed': 0, 'confirmed_by_alias': 0, 'possible': 0, 'not_found': 0}
    warnings: list[str] = []
    for _generation, platform, game in iter_catalog(catalog):
        name = _normalize_title(game['name'])
        if name in titles:
            counts['confirmed'] += 1
            continue
        alias = next((_normalize_title(item) for item in game.get('aliases', []) if _normalize_title(item) in titles), None)
        if alias:
            counts['confirmed_by_alias'] += 1
            continue
        candidates = [candidate for candidate in titles if abs(len(candidate) - len(name)) <= max(5, len(name) // 3)]
        possible = max(candidates, key=lambda item: SequenceMatcher(None, name, item).ratio(), default='')
        ratio = SequenceMatcher(None, name, possible).ratio() if possible else 0
        if ratio >= 0.86:
            counts['possible'] += 1
            warnings.append(
                f"Possível correspondência no inventário: {platform['name']} / {game['name']} -> {possible}"
            )
        else:
            counts['not_found'] += 1
            warnings.append(f"Jogo não confirmado no inventário: {platform['name']} / {game['name']}")
    return counts, warnings


def import_catalog(
    *, catalog_path: Path | str = DEFAULT_CATALOG_PATH, dry_run: bool = False,
    deactivate_missing: bool = False, inventory_path: Path | str | None = None,
) -> ImportReport:
    catalog = load_catalog(catalog_path)
    validate_catalog(catalog)
    report = _plan(catalog, deactivate_missing=deactivate_missing) if dry_run else _apply(
        catalog, deactivate_missing=deactivate_missing
    )
    if inventory_path:
        report.inventory, warnings = compare_inventory(catalog, inventory_path)
        report.warnings.extend(warnings)
    source_total = catalog.get('source_declared_total_minutes')
    calculated_total = catalog.get('total_estimated_minutes')
    if source_total and calculated_total and source_total != calculated_total:
        report.warnings.insert(
            0,
            'Total editorial divergente: o resumo da fonte declara '
            f'{source_total // 60} h, mas as linhas dos jogos somam {calculated_total // 60} h.',
        )
    return report
