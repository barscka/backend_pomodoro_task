import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.pomodoro.models import ExecutionIdempotency, History, PremiumPeriod, Schedule
from .activity_execution import ActivityExecutionConflict, get_active_schedule
from .premium_periods import bounds


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@transaction.atomic
def start_premium(*, period_id, scope_key, duration_minutes, request_id, return_group_id=None, continued_from_id=None, expected_version=None):
    if not getattr(settings, 'PREMIUM_DIRECT_START_ENABLED', False):
        raise ActivityExecutionConflict('premium_direct_disabled', 'Novos inícios premium diretos ainda não estão liberados.')
    payload = {'period_id': int(period_id), 'duration_minutes': int(duration_minutes), 'return_group_id': return_group_id, 'continued_from_id': continued_from_id}
    payload_hash = _hash(payload)
    existing_request = ExecutionIdempotency.objects.select_for_update().filter(scope_key=scope_key, request_id=request_id).first()
    if existing_request:
        if existing_request.payload_hash != payload_hash:
            raise ActivityExecutionConflict('idempotency_payload_conflict', 'A chave já foi usada com outro payload.')
        if existing_request.schedule:
            return existing_request.schedule, False
        raise ActivityExecutionConflict('idempotency_tombstone', 'A execução original desta chave não está mais disponível.')
    period = PremiumPeriod.objects.select_related('activity__category__group').select_for_update().get(pk=period_id)
    start, end = bounds(period)
    now = timezone.now()
    if not start <= now < end:
        raise ActivityExecutionConflict('premium_period_not_active', 'O período premium não está vigente.')
    if not 1 <= duration_minutes <= 720:
        raise ActivityExecutionConflict('invalid_duration', 'A duração deve estar entre 1 e 720 minutos.')
    predecessor = None
    if continued_from_id:
        predecessor = Schedule.objects.select_for_update().filter(pk=continued_from_id, scope_key=scope_key).first()
        if not predecessor:
            raise ActivityExecutionConflict('execution_not_found', 'Execução anterior não encontrada.')
        if predecessor.state != Schedule.STATE_COMPLETED or predecessor.version != expected_version:
            raise ActivityExecutionConflict('stale_execution_version', 'A execução anterior não está concluída na versão informada.')
        if predecessor.activity_id != period.activity_id or predecessor.premium_period_id != period.id:
            raise ActivityExecutionConflict('continuation_context_conflict', 'A execução não pertence ao mesmo jogo e período.')
        successor = Schedule.objects.filter(continued_from=predecessor).first()
        if successor:
            if (successor.planned_duration_seconds != duration_minutes * 60
                    or successor.return_group_id != return_group_id):
                raise ActivityExecutionConflict('continuation_payload_conflict', 'A execução anterior já possui continuação com outro payload.')
            ExecutionIdempotency.objects.create(scope_key=scope_key, request_id=request_id,
                payload_hash=payload_hash, schedule=successor, schedule_id_tombstone=successor.id)
            return successor, False
    active = get_active_schedule(scope_key)
    if active:
        raise ActivityExecutionConflict('active_execution_conflict', 'Já existe uma atividade em execução.', schedule=active)
    try:
        with transaction.atomic():
            schedule = Schedule.objects.create(
                activity=period.activity, scheduled_date=timezone.localdate(now),
                start_time=timezone.localtime(now).time().replace(tzinfo=None), scope_key=scope_key,
                goal_category_id_snapshot=period.activity.category_id,
                goal_group_id_snapshot=period.activity.category.group_id,
                state=Schedule.STATE_RUNNING, requested_at=now, starts_at=now,
                expected_end_at=now + timedelta(minutes=duration_minutes),
                execution_origin=Schedule.ORIGIN_PREMIUM_DIRECT, premium_period=period,
                planned_duration_seconds=duration_minutes * 60, continued_from=predecessor,
                return_group_id=return_group_id,
            )
    except IntegrityError:
        active = Schedule.objects.filter(scope_key=scope_key,
            state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING]).first()
        if active:
            raise ActivityExecutionConflict('active_execution_conflict', 'Já existe uma atividade em execução.', schedule=active)
        if predecessor:
            successor = Schedule.objects.filter(continued_from=predecessor).first()
            if successor:
                return successor, False
        raise ActivityExecutionConflict('premium_start_conflict', 'Não foi possível iniciar por conflito de persistência.')
    History.objects.create(activity=period.activity, schedule=schedule, start_time=now)
    try:
        ExecutionIdempotency.objects.create(scope_key=scope_key, request_id=request_id, payload_hash=payload_hash, schedule=schedule, schedule_id_tombstone=schedule.id)
    except IntegrityError:
        record = ExecutionIdempotency.objects.get(scope_key=scope_key, request_id=request_id)
        if record.payload_hash == payload_hash and record.schedule:
            return record.schedule, False
        raise ActivityExecutionConflict('idempotency_payload_conflict', 'A chave já foi usada com outro payload.')
    return schedule, True
