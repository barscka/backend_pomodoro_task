from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.pomodoro.models import ExecutionIdempotency, Group, History, RetroGame, Schedule
from .activity_execution import ActivityExecutionConflict, get_active_schedule
from .direct_execution import payload_hash
from .retro_progress import ensure_started


def _load_game(retro_game_id):
    try:
        return RetroGame.objects.select_related(
            'activity__category__group', 'platform__generation__group'
        ).select_for_update().get(pk=retro_game_id)
    except RetroGame.DoesNotExist:
        raise ActivityExecutionConflict('retro_game_not_found', 'Jogo retrô não encontrado.')


def _validate_game(game):
    if game.activity.category_id != game.platform.generation_id:
        raise ActivityExecutionConflict('retro_hierarchy_invalid', 'Activity e plataforma pertencem a gerações distintas.')
    if not game.platform.generation.group.is_retro_catalog:
        raise ActivityExecutionConflict('retro_hierarchy_invalid', 'O jogo não pertence ao catálogo RetroGames.')
    if not game.active or not game.platform.active or not game.activity.active:
        raise ActivityExecutionConflict('retro_game_inactive', 'O jogo, plataforma ou Activity está inativo.')


@transaction.atomic
def start_retro(*, retro_game_id, scope_key, duration_minutes, request_id,
                return_group_id=None, continued_from_id=None, expected_version=None):
    if not getattr(settings, 'RETROGAMES_ENABLED', False):
        raise ActivityExecutionConflict('retrogames_disabled', 'Novos inícios RetroGames ainda não estão liberados.')
    if not 1 <= int(duration_minutes) <= 720:
        raise ActivityExecutionConflict('invalid_duration', 'A duração deve estar entre 1 e 720 minutos.')
    if return_group_id and not Group.objects.filter(pk=return_group_id).exists():
        raise ActivityExecutionConflict('return_group_not_found', 'Grupo de retorno não encontrado.')

    payload = {
        'retro_game_id': int(retro_game_id),
        'duration_minutes': int(duration_minutes),
        'return_group_id': return_group_id,
        'continued_from_id': continued_from_id,
    }
    request_payload_hash = payload_hash(payload)
    request_record = ExecutionIdempotency.objects.select_for_update().filter(
        scope_key=scope_key, request_id=request_id
    ).first()
    if request_record:
        if request_record.payload_hash != request_payload_hash:
            raise ActivityExecutionConflict('idempotency_payload_conflict', 'A chave já foi usada com outro payload.')
        if request_record.schedule:
            return request_record.schedule, False
        raise ActivityExecutionConflict('idempotency_tombstone', 'A execução original desta chave não está mais disponível.')

    game = _load_game(retro_game_id)
    _validate_game(game)
    predecessor = None
    if continued_from_id:
        predecessor = Schedule.objects.select_for_update().filter(
            pk=continued_from_id, scope_key=scope_key
        ).first()
        if not predecessor:
            raise ActivityExecutionConflict('execution_not_found', 'Execução anterior não encontrada.')
        if predecessor.state != Schedule.STATE_COMPLETED or predecessor.version != expected_version:
            raise ActivityExecutionConflict('stale_execution_version', 'A execução anterior não está concluída na versão informada.')
        if predecessor.execution_origin != Schedule.ORIGIN_RETRO_DIRECT or predecessor.retro_game_id != game.id:
            raise ActivityExecutionConflict('continuation_context_conflict', 'A execução não pertence ao mesmo jogo RetroGames.')
        successor = Schedule.objects.filter(continued_from=predecessor).first()
        if successor:
            if (successor.planned_duration_seconds != int(duration_minutes) * 60
                    or successor.return_group_id != return_group_id):
                raise ActivityExecutionConflict('continuation_payload_conflict', 'A execução anterior já possui continuação com outro payload.')
            ExecutionIdempotency.objects.create(
                scope_key=scope_key, request_id=request_id, payload_hash=request_payload_hash,
                schedule=successor, schedule_id_tombstone=successor.id,
            )
            return successor, False

    active = get_active_schedule(scope_key)
    if active:
        raise ActivityExecutionConflict('active_execution_conflict', 'Já existe uma atividade em execução.', schedule=active)

    now = timezone.now()
    try:
        with transaction.atomic():
            schedule = Schedule.objects.create(
                activity=game.activity,
                scheduled_date=timezone.localdate(now),
                start_time=timezone.localtime(now).time().replace(tzinfo=None),
                scope_key=scope_key,
                goal_category_id_snapshot=game.activity.category_id,
                goal_group_id_snapshot=game.activity.category.group_id,
                state=Schedule.STATE_RUNNING,
                requested_at=now,
                starts_at=now,
                expected_end_at=now + timedelta(minutes=int(duration_minutes)),
                execution_origin=Schedule.ORIGIN_RETRO_DIRECT,
                retro_game=game,
                planned_duration_seconds=int(duration_minutes) * 60,
                continued_from=predecessor,
                return_group_id=return_group_id,
            )
    except IntegrityError:
        replay = ExecutionIdempotency.objects.filter(
            scope_key=scope_key, request_id=request_id
        ).first()
        if replay and replay.payload_hash == request_payload_hash and replay.schedule:
            return replay.schedule, False
        active = Schedule.objects.filter(
            scope_key=scope_key,
            state__in=[Schedule.STATE_PREPARING, Schedule.STATE_RUNNING],
        ).first()
        if active:
            raise ActivityExecutionConflict('active_execution_conflict', 'Já existe uma atividade em execução.', schedule=active)
        if predecessor:
            successor = Schedule.objects.filter(continued_from=predecessor).first()
            if successor:
                return successor, False
        raise ActivityExecutionConflict('retro_start_conflict', 'Não foi possível iniciar por conflito de persistência.')

    History.objects.create(activity=game.activity, schedule=schedule, start_time=now)
    ensure_started(retro_game=game, scope_key=scope_key)
    try:
        ExecutionIdempotency.objects.create(
            scope_key=scope_key, request_id=request_id, payload_hash=request_payload_hash,
            schedule=schedule, schedule_id_tombstone=schedule.id,
        )
    except IntegrityError:
        record = ExecutionIdempotency.objects.get(scope_key=scope_key, request_id=request_id)
        if record.payload_hash == request_payload_hash and record.schedule:
            return record.schedule, False
        raise ActivityExecutionConflict('idempotency_payload_conflict', 'A chave já foi usada com outro payload.')
    return schedule, True
