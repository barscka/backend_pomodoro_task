"""Prospective weekly revisions and per-occurrence guidance, using half-open intervals."""
from datetime import datetime, time, timedelta
from uuid import uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.pomodoro.models import (
    Activity, ActivityRoutinePreference, Group, PremiumPeriod, RoutineBlock,
    RoutineException, RoutinePlan, RoutineRevision, RoutineSelection, RoutineSuspension,
)
from apps.pomodoro.repositories.routines import plan_for

ZONE = ZoneInfo('America/Sao_Paulo')
BLOCK_FIELDS = ('block_id', 'weekday', 'kind', 'profile', 'start_time', 'end_time', 'end_day_offset', 'expected_min_minutes', 'expected_max_minutes')
PROFILE_FIELDS = ('pause_immediately', 'online', 'requires_group', 'competitive', 'focus_suitable', 'minimum_minutes')


class RoutineError(Exception):
    def __init__(self, code, detail, status=400, **payload):
        self.code, self.detail, self.status, self.payload = code, detail, status, payload
        super().__init__(detail)


def today():
    return timezone.now().astimezone(ZONE).date()


def midnight(day):
    return datetime.combine(day, time.min, ZONE)


def dates(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


def block_dict(block):
    return {key: str(getattr(block, key)) if key in ('block_id', 'start_time', 'end_time') else getattr(block, key) for key in BLOCK_FIELDS}


def plan_payload(scope):
    plan = plan_for(scope)
    if not plan:
        return {'state': 'no_plan', 'version': 0, 'timezone': str(ZONE), 'revisions': []}
    revisions = plan.revisions.prefetch_related('blocks')
    return {'state': 'planned' if revisions.exists() else 'no_plan', 'version': plan.version, 'timezone': plan.timezone,
            'revisions': [{'version': rev.version, 'effective_from': rev.effective_from,
                           'blocks': [block_dict(b) for b in rev.blocks.order_by('weekday', 'start_time', 'block_id')],
                           'nominal_capacity': nominal_capacity(list(rev.blocks.all()))} for rev in revisions]}


def nominal_capacity(blocks):
    totals = {'gameplay_seconds': 0, 'general_seconds': 0, 'interruptible_seconds': 0, 'family_seconds': 0, 'cardio_seconds': 0}
    daily = [0] * 7
    for block in blocks:
        row = occurrence(block_dict(block), today(), 0)
        seconds = row['duration_seconds']
        totals[block.kind + '_seconds'] += seconds
        if block.kind == 'gameplay':
            daily[block.weekday] += seconds
            totals['interruptible_seconds' if block.profile == 'interruptible' else 'general_seconds'] += seconds
    return {**totals, 'gameplay_seconds_by_weekday': daily, 'basis': 'origin_day_weekly_template'}


def locked_plan(scope, expected, *, create=False):
    if create:
        RoutinePlan.objects.get_or_create(scope_key=scope)
    plan = RoutinePlan.objects.select_for_update().filter(scope_key=scope).first()
    actual = plan.version if plan else 0
    if actual != expected:
        raise RoutineError('stale_routine_version', 'A rotina mudou. Recarregue antes de editar.', 409, current_version=actual)
    if not plan:
        raise RoutineError('not_found', 'Rotina não encontrada.', 404)
    return plan


def bump(plan):
    plan.version += 1
    plan.save(update_fields=['version', 'template_applied'])


def initial_blocks():
    output = []
    weekdays = [('18:00', '18:30', 0, 'cardio', 'general'), ('18:30', '20:00', 0, 'gameplay', 'general'),
                ('20:00', '22:30', 0, 'family', 'general'), ('22:30', '01:00', 1, 'gameplay', 'general')]
    weekend = [('07:00', '09:00', 0, 'gameplay', 'focus'), ('09:00', '12:00', 0, 'gameplay', 'interruptible'),
               ('13:00', '14:30', 0, 'family', 'general'), ('14:30', '18:00', 0, 'gameplay', 'general'),
               ('18:00', '18:30', 0, 'cardio', 'general'), ('19:00', '22:30', 0, 'family', 'general'),
               ('22:30', '01:00', 1, 'gameplay', 'general')]
    for day in range(7):
        for start, end, offset, kind, profile in weekdays if day < 5 else weekend:
            output.append({'block_id': uuid5(NAMESPACE_URL, f'pomodoro/routine/v1/{day}/{start}'), 'weekday': day,
                           'start_time': time.fromisoformat(start), 'end_time': time.fromisoformat(end),
                           'end_day_offset': offset, 'kind': kind, 'profile': profile,
                           'expected_min_minutes': 15 if kind == 'cardio' else None, 'expected_max_minutes': 30 if kind == 'cardio' else None})
    return output


def validate_week(blocks):
    identities = [str(b['block_id']) for b in blocks]
    if len(set(identities)) != len(identities):
        raise RoutineError('invalid_routine', 'IDs de bloco devem ser únicos na revisão.')
    monday = today() - timedelta(days=today().weekday())
    spans = []
    for week in (-1, 0, 1):
        for b in blocks:
            origin = monday + timedelta(days=7 * week + b['weekday'])
            spans.append(occurrence(b, origin, 0))
    validate_overlaps(spans)


def validate_overlaps(rows):
    rows = sorted(rows, key=lambda r: r['starts_at'])
    for a, b in zip(rows, rows[1:]):
        if a['ends_at'] > b['starts_at']:
            raise RoutineError('overlap', 'Blocos se sobrepõem após aplicar as alterações.', 409,
                               occurrences=[a['occurrence_id'], b['occurrence_id']])


@transaction.atomic
def save_revision(scope, data, *, template=False):
    plan = locked_plan(scope, data['expected_version'], create=True) if not template else None
    if template:
        RoutinePlan.objects.get_or_create(scope_key=scope)
        plan = RoutinePlan.objects.select_for_update().get(scope_key=scope)
        if plan.template_applied:
            return plan_payload(scope)
        if plan.version != data['expected_version']:
            raise RoutineError('stale_routine_version', 'A rotina mudou.', 409, current_version=plan.version)
        if plan.revisions.exists():
            raise RoutineError('template_requires_empty_plan', 'Modelo inicial só se aplica a uma rotina sem revisões.', 409)
    effective = data['effective_from']
    latest = plan.revisions.last()
    if effective < today() or (latest and (effective <= today() or effective <= latest.effective_from)):
        raise RoutineError('invalid_effective_date', 'Primeira vigência não retroage; edições começam amanhã ou após a última revisão.')
    blocks = initial_blocks() if template else data['blocks']
    validate_week(blocks)
    revision = RoutineRevision.objects.create(plan=plan, effective_from=effective, version=plan.version + 1)
    RoutineBlock.objects.bulk_create([RoutineBlock(revision=revision, **block) for block in blocks])
    # Check revision boundaries and previously saved future exceptions as well as the weekly cycle.
    affected = {effective}
    affected.update(plan.exceptions.filter(origin_date__gte=effective - timedelta(days=2)).values_list('origin_date', flat=True))
    for day in affected:
        expand(scope, day - timedelta(days=2), day + timedelta(days=2), validate=True)
    if template:
        plan.template_applied = True
    bump(plan)
    return plan_payload(scope)


def occurrence(block, origin, revision_version):
    start_day = block.get('starts_on', origin)
    if isinstance(start_day, str):
        start_day = datetime.fromisoformat(start_day).date()
    start = block['start_time']
    end = block['end_time']
    if isinstance(start, str):
        start = time.fromisoformat(start)
    if isinstance(end, str):
        end = time.fromisoformat(end)
    start_at = datetime.combine(start_day, start, ZONE)
    end_at = datetime.combine(start_day + timedelta(days=block['end_day_offset']), end, ZONE)
    return {'occurrence_id': f"{origin}:{block['block_id']}", 'block_id': str(block['block_id']), 'origin_date': origin.isoformat(),
            'revision_version': revision_version, 'kind': block['kind'], 'profile': block['profile'],
            'starts_at': start_at, 'ends_at': end_at,
            'cardio_expectation': {'minimum_minutes': block['expected_min_minutes'], 'maximum_minutes': block['expected_max_minutes']} if block.get('expected_min_minutes') is not None else None,
            'duration_seconds': int((end_at - start_at).total_seconds())}


def expand(scope, date_from, date_to, *, validate=False):
    plan = plan_for(scope)
    if not plan:
        return {'version': 0, 'timezone': str(ZONE), 'days': [{'date': d, 'state': 'no_plan'} for d in dates(date_from, date_to)], 'occurrences': []}
    revisions = list(plan.revisions.prefetch_related('blocks'))
    exceptions = {(e.origin_date, str(e.block_id)): e for e in plan.exceptions.filter(origin_date__range=(date_from - timedelta(days=2), date_to + timedelta(days=1)))}
    suspended = set(plan.suspensions.filter(date__range=(date_from - timedelta(days=2), date_to + timedelta(days=2))).values_list('date', flat=True))
    rows = []
    for origin in dates(date_from - timedelta(days=2), date_to + timedelta(days=1)):
        rev = next((r for r in reversed(revisions) if r.effective_from <= origin), None)
        if not rev:
            continue
        for b in rev.blocks.all():
            if b.weekday != origin.weekday():
                continue
            block = block_dict(b)
            exception = exceptions.get((origin, str(b.block_id)))
            if exception:
                if exception.action == 'cancel':
                    continue
                block.update(exception.replacement)
            row = occurrence(block, origin, rev.version)
            if row['ends_at'] <= midnight(date_from) or row['starts_at'] >= midnight(date_to + timedelta(days=1)):
                continue
            # Suspension is civil-day guidance, including midnight tails from yesterday.
            parts = [(row['starts_at'], row['ends_at'])]
            for day in sorted(suspended):
                left, right = midnight(day), midnight(day + timedelta(days=1))
                parts = [(s, e) for a, b in parts for s, e in [(a, min(b, left)), (max(a, right), b)] if s < e]
            for start, end in parts:
                rows.append({**row, 'starts_at': start, 'ends_at': end, 'duration_seconds': int((end - start).total_seconds())})
    validate_overlaps(rows)
    days = [{'date': d, 'state': 'suspended' if d in suspended else 'planned' if any(r.effective_from <= d for r in revisions) else 'no_plan'} for d in dates(date_from, date_to)]
    return {'version': plan.version, 'timezone': str(ZONE), 'days': days, 'occurrences': sorted(rows, key=lambda r: r['starts_at'])}


def occurrence_for(scope, origin_date, block_id):
    rows = expand(scope, origin_date - timedelta(days=1), origin_date + timedelta(days=2))['occurrences']
    matching = [r for r in rows if r['origin_date'] == origin_date.isoformat() and r['block_id'] == str(block_id)]
    if not matching:
        raise RoutineError('not_found', 'Ocorrência não encontrada ou suspensa/cancelada.', 404)
    now = timezone.now()
    return next((r for r in matching if r['starts_at'] <= now < r['ends_at']), matching[0])


def context(scope, *, at=None):
    now = (at or timezone.now()).astimezone(ZONE)
    expanded = expand(scope, now.date(), now.date() + timedelta(days=8))
    current = next((r for r in expanded['occurrences'] if r['starts_at'] <= now < r['ends_at']), None)
    following = next((r for r in expanded['occurrences'] if r['starts_at'] > now), None)
    gameplay = next((r for r in expanded['occurrences'] if r['kind'] == 'gameplay' and r['starts_at'] > now), None)
    state = expanded['days'][0]['state']
    return {'as_of': now, 'timezone': str(ZONE), 'version': expanded['version'],
            'state': current['kind'] if current else 'free' if state == 'planned' else state,
            'current': current, 'next_block': following, 'next_gameplay': gameplay,
            'remaining_seconds': int((current['ends_at'] - now).total_seconds()) if current else None}


@transaction.atomic
def save_exception(scope, data):
    plan = locked_plan(scope, data['expected_version'])
    origin = data['origin_date']
    if origin < today():
        raise RoutineError('historical_edit_forbidden', 'Exceções não alteram datas passadas.')
    # Load the unmodified revision occurrence, so cancellation can be replaced later.
    rev = plan.revisions.filter(effective_from__lte=origin).last()
    block = rev.blocks.filter(block_id=data['block_id'], weekday=origin.weekday()).first() if rev else None
    if not block:
        raise RoutineError('not_found', 'Bloco não encontrado na data.', 404)
    replacement = data.get('replacement', {})
    if replacement:
        if abs((replacement.get('starts_on', origin) - origin).days) > 1:
            raise RoutineError('invalid_routine', 'Movimento permite até um dia de deslocamento.')
        original = occurrence(block_dict(block), origin, rev.version)
        updated = occurrence({**replacement, 'block_id': block.block_id}, origin, rev.version)
        if updated['starts_at'] < midnight(today()):
            raise RoutineError('historical_edit_forbidden', 'Movimento não pode retroagir.')
        if data['action'] == 'shorten' and not (original['starts_at'] <= updated['starts_at'] < updated['ends_at'] <= original['ends_at']):
            raise RoutineError('invalid_routine', 'Encurtar exige intervalo contido no original.')
        if data['action'] in ('shorten', 'move') and (replacement['kind'], replacement['profile']) != (block.kind, block.profile):
            raise RoutineError('invalid_routine', 'Mover/encurtar preserva tipo e perfil; use substituir.')
        if data['action'] == 'move' and updated['duration_seconds'] != original['duration_seconds']:
            raise RoutineError('invalid_routine', 'Mover preserva a duração; use substituir para alterar.')
    serializable = {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in replacement.items()}
    RoutineException.objects.update_or_create(plan=plan, origin_date=origin, block_id=data['block_id'], defaults={'action': data['action'], 'replacement': serializable})
    expand(scope, origin - timedelta(days=2), origin + timedelta(days=2), validate=True)
    bump(plan)
    return {'version': plan.version}


@transaction.atomic
def save_suspension(scope, data):
    plan = locked_plan(scope, data['expected_version'])
    if data['date'] < today():
        raise RoutineError('historical_edit_forbidden', 'Suspensão não altera datas passadas.')
    if data['suspended']:
        RoutineSuspension.objects.get_or_create(plan=plan, date=data['date'])
    else:
        plan.suspensions.filter(date=data['date']).delete()
        expand(scope, data['date'] - timedelta(days=2), data['date'] + timedelta(days=2), validate=True)
    bump(plan)
    return {'version': plan.version}


def profile_payload(scope, activity_id):
    if not Activity.objects.filter(pk=activity_id).exists():
        raise RoutineError('not_found', 'Atividade não encontrada.', 404)
    pref = ActivityRoutinePreference.objects.filter(scope_key=scope, activity_id=activity_id).first()
    return {'activity_id': activity_id, 'version': pref.version if pref else 0, **{key: getattr(pref, key) if pref else None for key in PROFILE_FIELDS}}


@transaction.atomic
def save_profile(scope, data):
    # Lock the catalog row to serialize even the first classification per scope.
    if not Activity.objects.select_for_update().filter(pk=data['activity_id']).first():
        raise RoutineError('not_found', 'Atividade não encontrada.', 404)
    pref = ActivityRoutinePreference.objects.filter(scope_key=scope, activity_id=data['activity_id']).first()
    version = pref.version if pref else 0
    if data['expected_version'] != version:
        raise RoutineError('stale_preference_version', 'Classificação mudou.', 409, current_version=version)
    ActivityRoutinePreference.objects.update_or_create(scope_key=scope, activity_id=data['activity_id'], defaults={
        'version': version + 1, **{k: data[k] for k in PROFILE_FIELDS if k in data}})
    return profile_payload(scope, data['activity_id'])


def suitability(scope, activity_id, row, available_seconds, *, profile=None):
    pref = profile if profile is not None else profile_payload(scope, activity_id)
    bad, unknown, good = [], [], []
    if not row or row['kind'] != 'gameplay':
        return {'status': 'not_suitable', 'reasons': [{'code': 'outside_gameplay', 'detail': 'Sem recomendação proativa fora de gameplay.'}]}
    if row['profile'] == 'interruptible':
        for field, expected, label in [('pause_immediately', True, 'Pausa imediata'), ('online', False, 'Sem online'), ('requires_group', False, 'Solo'), ('competitive', False, 'Sem competitivo')]:
            value = pref[field]
            target = unknown if value is None else good if value == expected else bad
            target.append({'code': f'{field}_unknown' if value is None else f'{field}_compatible' if value == expected else f'{field}_incompatible', 'detail': label + (' não classificado.' if value is None else ' compatível.' if value == expected else ' incompatível com interrupções.')})
    if row['profile'] == 'focus':
        value = pref['focus_suitable']
        (unknown if value is None else good if value else bad).append({'code': 'focus_unknown' if value is None else 'focus_compatible' if value else 'focus_incompatible', 'detail': 'Adequação a foco ' + ('desconhecida.' if value is None else 'confirmada.' if value else 'incompatível.')})
    minimum = pref['minimum_minutes']
    if minimum is None:
        # Casual compatibility is enough to recommend; minimum duration remains optional.
        if row['profile'] != 'interruptible':
            unknown.append({'code': 'minimum_duration_unknown', 'detail': 'Duração mínima útil não classificada.'})
    elif minimum * 60 > available_seconds:
        bad.append({'code': 'minimum_exceeds_window', 'detail': 'Duração mínima útil ultrapassa a janela.'})
    else:
        good.append({'code': 'minimum_fits', 'detail': 'Duração mínima útil cabe na janela.'})
    return {'status': 'not_suitable' if bad else 'insufficient_classification' if unknown else 'recommended', 'reasons': bad + unknown + good}


@transaction.atomic
def save_selection(scope, data):
    plan = locked_plan(scope, data['expected_version'])
    row = occurrence_for(scope, data['origin_date'], data['block_id'])
    if row['kind'] != 'gameplay':
        raise RoutineError('invalid_routine', 'Seleção exige ocorrência de gameplay.')
    if row['ends_at'] <= timezone.now():
        raise RoutineError('occurrence_ended', 'Ocorrência já encerrada.', 409)
    for field, model in [('premium_period_id', PremiumPeriod), ('group_id', Group), ('return_group_id', Group)]:
        if data.get(field) and not model.objects.filter(pk=data[field]).exists():
            raise RoutineError('not_found', 'Fonte ou grupo não encontrado.', 404)
    RoutineSelection.objects.update_or_create(plan=plan, origin_date=data['origin_date'], block_id=data['block_id'], defaults={
        'source': data['source'], 'premium_period_id': data.get('premium_period_id'), 'group_id': data.get('group_id'), 'return_group_id': data.get('return_group_id')})
    bump(plan)
    return selection_payload(scope, data['origin_date'], data['block_id'])


def selection_payload(scope, origin_date, block_id):
    plan = plan_for(scope)
    selection = RoutineSelection.objects.filter(plan=plan, origin_date=origin_date, block_id=block_id).first() if plan else None
    return {'version': plan.version if plan else 0, 'origin_date': origin_date, 'block_id': str(block_id),
            'selection': {k: getattr(selection, k) for k in ('source', 'premium_period_id', 'group_id', 'return_group_id')} if selection else None}


def recommendations(scope, page):
    ctx = context(scope)
    rows = []
    if ctx['state'] == 'gameplay':
        profiles = {pref.activity_id: {field: getattr(pref, field) for field in PROFILE_FIELDS}
                    for pref in ActivityRoutinePreference.objects.filter(scope_key=scope)}
        unknown = dict.fromkeys(PROFILE_FIELDS)
        for activity in Activity.objects.filter(active=True).order_by('id'):
            fit = suitability(scope, activity.id, ctx['current'], ctx['remaining_seconds'], profile=profiles.get(activity.id, unknown))
            if fit['status'] == 'recommended':
                rows.append({'activity_id': activity.id, 'name': activity.name, 'suitability': fit})
    return {'context': ctx, 'count': len(rows), 'page': page, 'page_size': 100, 'results': rows[(page - 1) * 100:page * 100]}
