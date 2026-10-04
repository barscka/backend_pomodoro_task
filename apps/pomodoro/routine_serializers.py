from datetime import date

from rest_framework import serializers


class RoutineDateField(serializers.DateField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not date(2000, 1, 1) <= value <= date(2100, 12, 31):
            raise serializers.ValidationError('Data deve estar entre 2000 e 2100.')
        return value


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError('Esperado objeto JSON.')
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'Campo não permitido.' for key in sorted(unknown)})
        return super().to_internal_value(data)


class BlockSerializer(StrictSerializer):
    block_id = serializers.UUIDField()
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    kind = serializers.ChoiceField(choices=['gameplay', 'cardio', 'family'])
    profile = serializers.ChoiceField(choices=['general', 'focus', 'interruptible'])
    start_time = serializers.TimeField(input_formats=['%H:%M', '%H:%M:%S'])
    end_time = serializers.TimeField(input_formats=['%H:%M', '%H:%M:%S'])
    end_day_offset = serializers.IntegerField(min_value=0, max_value=1, default=0)
    expected_min_minutes = serializers.IntegerField(min_value=1, max_value=1440, allow_null=True, default=None)
    expected_max_minutes = serializers.IntegerField(min_value=1, max_value=1440, allow_null=True, default=None)

    def validate(self, data):
        if not data['end_day_offset'] and data['end_time'] <= data['start_time']:
            raise serializers.ValidationError('Fim deve ser posterior ao início.')
        if data['kind'] != 'gameplay' and data['profile'] != 'general':
            raise serializers.ValidationError('Perfil especial exige gameplay.')
        low, high = data['expected_min_minutes'], data['expected_max_minutes']
        if (low is None) != (high is None) or (low is not None and (low > high or data['kind'] != 'cardio')):
            raise serializers.ValidationError('Expectativa min/max exige cardio e intervalo válido.')
        return data


class ReplacementSerializer(BlockSerializer):
    block_id = None
    weekday = None
    starts_on = RoutineDateField(required=False)


class VersionSerializer(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=0)


class RevisionSerializer(VersionSerializer):
    effective_from = RoutineDateField()
    blocks = BlockSerializer(many=True, allow_empty=True, max_length=64)


class TemplateSerializer(VersionSerializer):
    effective_from = RoutineDateField()


class ExceptionSerializer(VersionSerializer):
    origin_date = RoutineDateField()
    block_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=['cancel', 'move', 'shorten', 'replace'])
    replacement = ReplacementSerializer(required=False)

    def validate(self, data):
        if (data['action'] == 'cancel') == ('replacement' in data):
            raise serializers.ValidationError('Cancelamento não aceita replacement; demais ações exigem replacement.')
        return data


class SuspensionSerializer(VersionSerializer):
    date = RoutineDateField()
    suspended = serializers.BooleanField()


class IdentitySerializer(StrictSerializer):
    origin_date = RoutineDateField()
    block_id = serializers.UUIDField()


class SelectionSerializer(IdentitySerializer):
    expected_version = serializers.IntegerField(min_value=0)
    source = serializers.ChoiceField(choices=['premium', 'queue'])
    premium_period_id = serializers.IntegerField(min_value=1, required=False)
    group_id = serializers.IntegerField(min_value=1, required=False)
    return_group_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)

    def validate(self, data):
        premium = data['source'] == 'premium'
        if premium != ('premium_period_id' in data) or premium == ('group_id' in data):
            raise serializers.ValidationError('Informe exclusivamente o ID da fonte escolhida.')
        return data


class ActivityPreferenceSerializer(VersionSerializer):
    activity_id = serializers.IntegerField(min_value=1)
    pause_immediately = serializers.BooleanField(allow_null=True, required=False)
    online = serializers.BooleanField(allow_null=True, required=False)
    requires_group = serializers.BooleanField(allow_null=True, required=False)
    competitive = serializers.BooleanField(allow_null=True, required=False)
    focus_suitable = serializers.BooleanField(allow_null=True, required=False)
    minimum_minutes = serializers.IntegerField(min_value=1, max_value=720, allow_null=True, required=False)


class StartSerializer(IdentitySerializer):
    preview_token = serializers.CharField(max_length=8192)
    request_id = serializers.UUIDField()
    duration_minutes = serializers.IntegerField(min_value=1, max_value=720, required=False)


class IntervalSerializer(StrictSerializer):
    date_from = RoutineDateField()
    date_to = RoutineDateField()

    def validate(self, data):
        days = (data['date_to'] - data['date_from']).days
        if not 0 <= days < 31:
            raise serializers.ValidationError('Intervalo deve ter entre 1 e 31 dias inclusivos.')
        return data


class TrackingSettingsSerializer(VersionSerializer):
    daily_reference_minutes = serializers.IntegerField(min_value=1, max_value=1440, required=False)
    group_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, max_length=1000)
    reference_source = serializers.ChoiceField(choices=['fixed_daily', 'routine'], required=False)


class AgendaSerializer(IntervalSerializer):
    sessions_page = serializers.IntegerField(min_value=1, max_value=1000000, default=1)
