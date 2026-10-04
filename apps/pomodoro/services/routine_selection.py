"""Read-only signed previews, followed by explicit starts through canonical services."""
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.pomodoro.models import (
    ActivityQueueItem, PremiumPeriod, RoutinePlan, RoutineSelection, RoutineStartRequest,
)
from apps.pomodoro.repositories.routines import canonical_queue_item, open_execution, plan_for
from .activity_execution import start_activity
from .activity_queue import _ineligibility_reason, activity_is_eligible
from .direct_execution import payload_hash
from .premium_execution import start_premium
from .premium_periods import bounds
from .routines import RoutineError, context, occurrence_for, profile_payload, suitability

SALT = 'pomodoro.routine.preview.v1'


def preview(scope, origin_date, block_id):
    plan = plan_for(scope)
    row = occurrence_for(scope, origin_date, block_id)
    selection = RoutineSelection.objects.filter(plan=plan, origin_date=origin_date, block_id=block_id).first()
    now = timezone.now()
    current = context(scope, at=now)
    result = {'version': plan.version, 'occurrence': row, 'source': selection.source if selection else None,
              'availability': 'no_selection', 'reason': 'Selecione uma fonte para esta ocorrência.',
              'activity': None, 'premium_period_id': None, 'queue': None,
              'duration_minutes': None, 'suggested_duration_minutes': None, 'suitability': None,
              'predicted_end_at': None, 'crosses_next_block': False, 'return_group_id': selection.return_group_id if selection else None}
    activity, source_version = None, None
    if selection and selection.source == 'premium':
        period = PremiumPeriod.objects.select_related('activity__category__group').get(pk=selection.premium_period_id)
        activity = period.activity
        source_version = {'period_id': period.id, 'version': period.version, 'starts_on': str(period.starts_on),
                          'ends_on': str(period.ends_on), 'ended_early_at': str(period.ended_early_at)}
        p0, p1 = bounds(period)
        result['premium_period_id'] = period.id
        result['premium_period'] = {'id': period.id, 'title': period.title, 'starts_at': p0, 'ends_at': p1, 'version': period.version}
        if not p0 <= now < p1:
            result.update(availability='premium_not_active', reason='Preferência Premium vencida ou ainda não vigente.')
        elif not getattr(settings, 'PREMIUM_DIRECT_START_ENABLED', False):
            result.update(availability='premium_direct_disabled', reason='Início Premium direto não está liberado.')
        elif not activity.active:
            result.update(availability='activity_inactive', reason='Atividade inativa.')
        else:
            result.update(availability='available', reason=None)
        result['available_at_occurrence_start'] = p0 <= row['starts_at'] < p1
    elif selection:
        queue, item = canonical_queue_item(scope, selection.group_id)
        result['group_id'] = selection.group_id
        result['return_group_id'] = selection.group_id
        if not queue:
            result.update(availability='queue_absent', reason='Fila ausente. Apresente/crie a fila pelo fluxo explícito existente.')
        else:
            result['queue'] = {'id': queue.id, 'group_id': queue.group_id, 'group_name': queue.group.name,
                               'mode': queue.mode, 'skip_locked': queue.skip_locked, 'item_id': item.id if item else None,
                               'item_state': item.state if item else None, 'position': item.position if item else None}
            source_version = result['queue']
            if not item:
                result.update(availability='review_blocked' if queue.mode == 'skipped_review' else 'queue_empty', reason='Sem item operacional. Atualize a fila por ação explícita.')
            else:
                activity = item.activity
                eligible = activity_is_eligible(activity, queue.group, include_done_today=queue.mode == 'skipped_review')
                reason = None if eligible else _ineligibility_reason(activity, queue.group) or 'activity_no_longer_eligible'
                messages = {'inactive': 'Atividade inativa.', 'category_unavailable': 'Categoria indisponível.',
                            'group_mismatch': 'Atividade não pertence mais ao grupo.',
                            'category_daily_limit_reached': 'Limite diário da categoria atingido.',
                            'already_completed_today': 'Atividade já concluída hoje.',
                            'active_execution_conflict': 'Há execução aberta desta atividade.',
                            'group_daily_minutes_reached': 'Duração excede o saldo diário do grupo.',
                            'activity_no_longer_eligible': 'Atividade não está elegível para esta fila.'}
                result.update(availability=('review_blocked' if queue.mode == 'skipped_review' else 'queue_ineligible') if reason else 'available',
                              reason=messages.get(reason, reason), reason_code=reason)
    if activity:
        duration = activity.duration
        # Guidance for the selected occurrence; if ongoing, use remaining time.
        available_seconds = max(int((row['ends_at'] - max(now, row['starts_at'])).total_seconds()), 0)
        result['activity'] = {'id': activity.id, 'name': activity.name, 'duration_minutes': duration}
        result['duration_minutes'] = duration
        result['suggested_duration_minutes'] = max(1, min(duration, 720, available_seconds // 60)) if selection.source == 'premium' and available_seconds >= 60 else duration
        result['suitability'] = suitability(scope, activity.id, row, available_seconds)
        result['predicted_end_at'] = now + timedelta(minutes=result['suggested_duration_minutes'])
        boundary = current['current']['ends_at'] if current['current'] else current['next_block']['starts_at'] if current['next_block'] else None
        result['crosses_next_block'] = bool(boundary and result['predicted_end_at'] > boundary)
    active = open_execution(scope)  # Deliberately does NOT reconcile overdue executions.
    if active:
        result.update(availability='active_execution_conflict', reason='Há uma execução aberta; consulte o fluxo de execução para reconciliar.', active_execution_id=active.id)
    snapshot = {'scope': scope, 'plan_version': plan.version, 'origin_date': origin_date.isoformat(), 'block_id': str(block_id),
                'source': result['source'], 'source_version': source_version, 'activity': result['activity'],
                'profile_version': profile_payload(scope, activity.id)['version'] if activity else 0,
                'availability': result['availability'], 'return_group_id': result['return_group_id'],
                'context_occurrence': current['current']['occurrence_id'] if current['current'] else None,
                'context_state': current['state'], 'selected_occurrence': row['occurrence_id']}
    result['preview_token'] = signing.dumps(snapshot, salt=SALT, compress=True)
    return result, snapshot


@transaction.atomic
def start_from_preview(scope, data):
    plan = RoutinePlan.objects.select_for_update().filter(scope_key=scope).first()
    if not plan:
        raise RoutineError('not_found', 'Rotina não encontrada.', 404)
    fingerprint = payload_hash({k: str(v) for k, v in data.items() if k != 'request_id'})
    previous = RoutineStartRequest.objects.filter(plan=plan, request_id=data['request_id']).first()
    if previous:
        if previous.payload_hash != fingerprint:
            raise RoutineError('idempotency_payload_conflict', 'Chave usada com outro pedido.', 409)
        if not previous.schedule:
            raise RoutineError('idempotency_tombstone', 'Execução original indisponível.', 409)
        return previous.schedule, False
    try:
        expected = signing.loads(data['preview_token'], salt=SALT, max_age=900)
    except signing.BadSignature:
        payload = {'context': context(scope)}
        try:
            payload['preview'] = preview(scope, data['origin_date'], data['block_id'])[0]
        except RoutineError:
            pass
        raise RoutineError('stale_preview', 'Prévia inválida ou vencida. Consulte novamente.', 409, **payload)
    if expected.get('scope') != scope or expected.get('origin_date') != data['origin_date'].isoformat() or expected.get('block_id') != str(data['block_id']):
        raise RoutineError('stale_preview', 'Prévia não pertence à ocorrência/escopo.', 409)
    # Match canonical item under the SAME lock used by start_activity/skip_item.
    selection = RoutineSelection.objects.filter(plan=plan, origin_date=data['origin_date'], block_id=data['block_id']).first()
    if selection and selection.source == 'queue':
        queue, item = canonical_queue_item(scope, selection.group_id)
        if item:
            ActivityQueueItem.objects.select_related('queue__group', 'activity__category').select_for_update().get(pk=item.id)
    elif selection:
        PremiumPeriod.objects.select_related('activity__category__group').select_for_update().get(pk=selection.premium_period_id)
    try:
        updated, actual = preview(scope, data['origin_date'], data['block_id'])
    except RoutineError as exc:
        raise RoutineError('stale_preview', 'Ocorrência mudou ou foi suspensa/cancelada.', 409) from exc
    if expected != actual:
        raise RoutineError('stale_preview', 'Contexto mudou. Confira a nova prévia.', 409, preview=updated)
    if updated['availability'] != 'available':
        raise RoutineError('source_unavailable', updated['reason'] or 'Fonte indisponível.', 409, preview=updated)
    if selection.source == 'premium':
        duration = data.get('duration_minutes', updated['suggested_duration_minutes'])
        schedule, created = start_premium(period_id=selection.premium_period_id, scope_key=scope,
                                         duration_minutes=duration, request_id=data['request_id'], return_group_id=selection.return_group_id)
    else:
        if 'duration_minutes' in data and data['duration_minutes'] != updated['duration_minutes']:
            raise RoutineError('queue_duration_fixed', 'Duração da fila deve ser preservada.')
        item = ActivityQueueItem.objects.select_related('activity').get(pk=updated['queue']['item_id'])
        schedule, created = start_activity(activity=item.activity, queue_item=item, scope_key=scope)
        if schedule.return_group_id is None:
            schedule.return_group_id = selection.group_id
            schedule.save(update_fields=['return_group_id'])
    if created:
        requested = {'occurrence_id': f"{data['origin_date']}:{data['block_id']}",
                     'origin_date': data['origin_date'].isoformat(), 'block_id': str(data['block_id'])}
        schedule.routine_requested_occurrence = requested
        schedule.save(update_fields=['routine_requested_occurrence'])
        from apps.pomodoro.models import RoutineSessionAssociation
        RoutineSessionAssociation.objects.filter(schedule=schedule).update(requested_occurrence=requested)
    RoutineStartRequest.objects.create(plan=plan, request_id=data['request_id'], payload_hash=fingerprint, schedule=schedule)
    return schedule, created
