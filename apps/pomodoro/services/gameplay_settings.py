from django.db import transaction

from apps.pomodoro.models import GameplayTrackingSettings, Group
from .routines import RoutineError


@transaction.atomic
def update_settings(scope, data):
    GameplayTrackingSettings.objects.get_or_create(scope_key=scope, defaults={'version': 0})
    obj = GameplayTrackingSettings.objects.select_for_update().get(scope_key=scope)
    if data['expected_version'] != obj.version:
        raise RoutineError('stale_settings_version', 'Configurações mudaram.', 409)
    groups = None
    if 'group_ids' in data:
        groups = Group.objects.filter(id__in=data['group_ids'])
        if groups.count() != len(set(data['group_ids'])):
            raise RoutineError('group_not_found', 'Grupo não encontrado.')
    for field in ('daily_reference_minutes', 'reference_source'):
        if field in data:
            setattr(obj, field, data[field])
    obj.version += 1
    obj.save()
    if groups is not None:
        obj.groups.set(groups)
