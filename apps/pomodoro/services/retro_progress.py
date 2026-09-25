from django.db import transaction
from django.utils import timezone

from apps.pomodoro.models import RetroGameProgress


class RetroProgressConflict(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@transaction.atomic
def set_progress(*, retro_game, scope_key, status, expected_version):
    current = RetroGameProgress.objects.select_for_update().filter(
        retro_game=retro_game, scope_key=scope_key
    ).first()
    current_version = current.version if current else 0
    if current and current.status == status:
        return current, False
    if int(expected_version) != current_version:
        raise RetroProgressConflict('stale_progress_version', 'A versão do progresso está desatualizada.')

    now = timezone.now()
    if current is None:
        current = RetroGameProgress(
            retro_game=retro_game,
            scope_key=scope_key,
            version=1,
            started_at=now if status in ['in_progress', 'completed'] else None,
        )
    else:
        current.version += 1
        if status in ['in_progress', 'completed'] and current.started_at is None:
            current.started_at = now
    current.status = status
    current.completed_at = now if status == RetroGameProgress.STATUS_COMPLETED else None
    current.full_clean()
    current.save()
    return current, True


def ensure_started(*, retro_game, scope_key):
    """First direct start begins the plan, without reopening completed/skipped games."""
    progress, _ = RetroGameProgress.objects.get_or_create(
        retro_game=retro_game,
        scope_key=scope_key,
        defaults={
            'status': RetroGameProgress.STATUS_IN_PROGRESS,
            'started_at': timezone.now(),
        },
    )
    return progress
