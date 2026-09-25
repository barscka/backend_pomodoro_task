# apps/pomodoro/serializers.py
from datetime import timezone as datetime_timezone

from rest_framework import serializers
from .models import (
    Activity,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
    PremiumPeriod,
    RetroGame,
    RetroGameProgress,
    RetroPlatform,
)
from .services.activity_queue import group_daily_metrics


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'color', 'is_default', 'is_retro_catalog',
                  'max_daily_minutes']

class CategorySerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'color', 'group', 'group_name']

class ActivitySerializer(serializers.ModelSerializer):
    can_execute = serializers.SerializerMethodField()
    remaining_executions = serializers.SerializerMethodField()
    group_id = serializers.IntegerField(source='category.group_id', read_only=True)
    group_name = serializers.CharField(source='category.group.name', read_only=True)
    is_premium_active = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Activity
        fields = ['id', 'name', 'description', 'duration', 'active', 'premium',
                 'premium_from', 'premium_until', 'is_premium_active', 'category',
                 'created_at', 'last_executed', 'executions_today',
                 'can_execute', 'remaining_executions', 'group_id', 'group_name']

    def validate(self, attrs):
        if self.instance and self.instance.premium_periods.exists() and any(
            key in attrs for key in ('premium', 'premium_from', 'premium_until')
        ):
            raise serializers.ValidationError(
                'A vigência desta atividade é gerenciada por premium-periods.'
            )
        premium = attrs.get('premium', self.instance.premium if self.instance else False)
        premium_from = attrs.get(
            'premium_from', self.instance.premium_from if self.instance else None
        )
        premium_until = attrs.get(
            'premium_until', self.instance.premium_until if self.instance else None
        )
        if premium and (not premium_from or not premium_until):
            raise serializers.ValidationError(
                'Atividades premium precisam informar premium_from e premium_until.'
            )
        if premium and premium_from > premium_until:
            raise serializers.ValidationError(
                'premium_from não pode ser maior que premium_until.'
            )
        return attrs

    def _get_selected_group(self):
        request = self.context.get('request')
        if not request:
            return None

        group_id = request.query_params.get('group_id') or request.data.get('group_id')
        if not group_id:
            return None

        try:
            return Group.objects.filter(pk=group_id).first()
        except (TypeError, ValueError):
            return None
    
    def get_can_execute(self, obj):
        return obj.can_execute(self._get_selected_group())
    
    def get_remaining_executions(self, obj):
        return obj.remaining_executions(self._get_selected_group())

# apps/pomodoro/serializers.py
class HistorySerializer(serializers.ModelSerializer):
    activity_name = serializers.CharField(source='activity.name', read_only=True)
    category_name = serializers.CharField(source='activity.category.name', read_only=True)
    group_name = serializers.CharField(source='activity.category.group.name', read_only=True)
    completed = serializers.SerializerMethodField()

    class Meta:
        model = History
        fields = [
            'id',
            'activity_name',
            'category_name',
            'group_name',
            'start_time',
            'end_time',
            'duration',
            'completed'
        ]

    def get_completed(self, obj):
        """Determina se a atividade foi completada baseado no end_time"""
        return obj.end_time is not None


class QueueContextSerializerMixin:
    def _queue(self, obj):
        if isinstance(obj, ActivityQueueItem):
            return obj.queue
        if not obj.queue_item_id:
            return None
        return obj.queue_item.queue

    def _group_metrics(self, obj):
        queue = self._queue(obj)
        if queue is None:
            return None
        provided = self.context.get('group_daily_metrics')
        if provided is not None:
            return provided
        cache = getattr(self, '_group_metrics_cache', None)
        if cache is None or cache[0] != queue.group_id:
            cache = (queue.group_id, group_daily_metrics(queue.group))
            self._group_metrics_cache = cache
        return cache[1]

    def get_queue_id(self, obj):
        queue = self._queue(obj)
        return queue.id if queue else None

    def get_queue_group_id(self, obj):
        queue = self._queue(obj)
        return queue.group_id if queue else None

    def get_queue_group_name(self, obj):
        queue = self._queue(obj)
        return queue.group.name if queue else None

    def get_queue_mode(self, obj):
        queue = self._queue(obj)
        return queue.mode if queue else None

    def get_skip_locked(self, obj):
        queue = self._queue(obj)
        return queue.skip_locked if queue else None

    def get_group_max_daily_minutes(self, obj):
        metrics = self._group_metrics(obj)
        return metrics['group_max_daily_minutes'] if metrics else None

    def get_group_consumed_daily_minutes(self, obj):
        metrics = self._group_metrics(obj)
        return metrics['group_consumed_daily_minutes'] if metrics else None

    def get_group_remaining_daily_minutes(self, obj):
        metrics = self._group_metrics(obj)
        return metrics['group_remaining_daily_minutes'] if metrics else None


class ActivityQueueItemSerializer(QueueContextSerializerMixin, serializers.ModelSerializer):
    activity = ActivitySerializer(read_only=True)
    queue_item_id = serializers.IntegerField(source='id', read_only=True)
    queue_id = serializers.SerializerMethodField()
    queue_mode = serializers.SerializerMethodField()
    pool_number = serializers.IntegerField(source='queue.pool_number', read_only=True)
    pool_size = serializers.IntegerField(source='queue.pool_size', read_only=True)
    consumed_count = serializers.IntegerField(source='queue.consumed_count', read_only=True)
    skip_locked = serializers.SerializerMethodField()
    queue_group_id = serializers.SerializerMethodField()
    queue_group_name = serializers.SerializerMethodField()
    position = serializers.IntegerField(read_only=True)
    source_queue_id = serializers.IntegerField(source='queue.source_queue_id', read_only=True)
    group_max_daily_minutes = serializers.SerializerMethodField()
    group_consumed_daily_minutes = serializers.SerializerMethodField()
    group_remaining_daily_minutes = serializers.SerializerMethodField()
    id = serializers.IntegerField(source='activity.id', read_only=True)
    name = serializers.CharField(source='activity.name', read_only=True)
    description = serializers.CharField(source='activity.description', read_only=True)
    duration = serializers.IntegerField(source='activity.duration', read_only=True)
    category = serializers.IntegerField(source='activity.category_id', read_only=True)
    group_id = serializers.IntegerField(source='activity.category.group_id', read_only=True)
    group_name = serializers.CharField(source='activity.category.group.name', read_only=True)
    premium = serializers.BooleanField(source='activity.premium', read_only=True)
    is_premium_active = serializers.BooleanField(source='activity.is_premium_active', read_only=True)

    class Meta:
        model = ActivityQueueItem
        fields = [
            'queue_item_id',
            'queue_id',
            'id',
            'name',
            'description',
            'duration',
            'category',
            'group_id',
            'group_name',
            'premium',
            'is_premium_active',
            'activity',
            'queue_mode',
            'pool_number',
            'pool_size',
            'consumed_count',
            'skip_locked',
            'queue_group_id',
            'queue_group_name',
            'position',
            'source_queue_id',
            'group_max_daily_minutes',
            'group_consumed_daily_minutes',
            'group_remaining_daily_minutes',
            'state',
        ]


class ActivityExecutionSerializer(QueueContextSerializerMixin, serializers.ModelSerializer):
    activity = ActivitySerializer(read_only=True)
    queue_id = serializers.SerializerMethodField()
    queue_item_id = serializers.IntegerField(read_only=True, allow_null=True)
    queue_group_id = serializers.SerializerMethodField()
    queue_group_name = serializers.SerializerMethodField()
    queue_mode = serializers.SerializerMethodField()
    skip_locked = serializers.SerializerMethodField()
    group_max_daily_minutes = serializers.SerializerMethodField()
    group_consumed_daily_minutes = serializers.SerializerMethodField()
    group_remaining_daily_minutes = serializers.SerializerMethodField()
    execution_id = serializers.IntegerField(source='id', read_only=True)
    requested_at = serializers.DateTimeField(
        default_timezone=datetime_timezone.utc,
        read_only=True,
    )
    starts_at = serializers.DateTimeField(
        default_timezone=datetime_timezone.utc,
        read_only=True,
    )
    expected_end_at = serializers.DateTimeField(
        default_timezone=datetime_timezone.utc,
        read_only=True,
    )
    completed_at = serializers.DateTimeField(
        default_timezone=datetime_timezone.utc,
        read_only=True,
    )
    server_now = serializers.SerializerMethodField()
    remaining_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Schedule
        fields = [
            'execution_id',
            'id',
            'queue_id',
            'queue_item_id',
            'queue_group_id',
            'queue_group_name',
            'queue_mode',
            'skip_locked',
            'group_max_daily_minutes',
            'group_consumed_daily_minutes',
            'group_remaining_daily_minutes',
            'state',
            'activity',
            'requested_at',
            'starts_at',
            'expected_end_at',
            'completed_at',
            'remaining_seconds',
            'server_now',
            'version',
            'execution_origin',
            'planned_duration_seconds',
            'premium_period_id',
            'retro_game_id',
            'continued_from_id',
            'return_group_id',
        ]

    def get_server_now(self, _obj):
        from django.utils import timezone

        return timezone.now()

    def get_remaining_seconds(self, obj):
        from django.utils import timezone

        if not obj.expected_end_at:
            return 0
        delta = obj.expected_end_at - timezone.now()
        return max(int(delta.total_seconds()), 0)


class PremiumPeriodSerializer(serializers.ModelSerializer):
    activity_name = serializers.CharField(source='activity.name', read_only=True)
    activity_group_id = serializers.IntegerField(source='activity.category.group_id', read_only=True)
    activity_group_name = serializers.CharField(source='activity.category.group.name', read_only=True)
    effective_end_at = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = PremiumPeriod
        fields = ['id', 'activity', 'activity_name', 'activity_group_id', 'activity_group_name',
                  'kind', 'title', 'starts_on', 'ends_on', 'timezone', 'ended_early_at',
                  'effective_end_at', 'status', 'source', 'version', 'created_at', 'updated_at']
        read_only_fields = ['ended_early_at', 'source', 'version', 'created_at', 'updated_at']

    def get_effective_end_at(self, obj):
        from .services.premium_periods import bounds
        return bounds(obj)[1]

    def get_status(self, obj):
        from django.utils import timezone
        from .services.premium_periods import bounds
        start, end = bounds(obj)
        now = timezone.now()
        return 'future' if now < start else ('active' if now < end else 'ended')

    def validate(self, attrs):
        starts = attrs.get('starts_on', getattr(self.instance, 'starts_on', None))
        ends = attrs.get('ends_on', getattr(self.instance, 'ends_on', None))
        if starts and ends and starts > ends:
            raise serializers.ValidationError({'ends_on': 'A data final deve ser igual ou posterior à inicial.'})
        timezone_name = attrs.get('timezone', getattr(self.instance, 'timezone', 'America/Sao_Paulo'))
        if timezone_name != 'America/Sao_Paulo':
            raise serializers.ValidationError({'timezone': 'O fuso desta versão deve ser America/Sao_Paulo.'})
        return attrs


class PremiumStartSerializer(serializers.Serializer):
    duration_minutes = serializers.IntegerField(min_value=1, max_value=720)
    request_id = serializers.UUIDField()
    return_group_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class PremiumContinueSerializer(PremiumStartSerializer):
    expected_version = serializers.IntegerField(min_value=1)


class RetroGenerationSerializer(serializers.ModelSerializer):
    sort_order = serializers.IntegerField(source='retro_sort_order')

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'color', 'sort_order']


class RetroPlatformSerializer(serializers.ModelSerializer):
    generation_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = RetroPlatform
        fields = ['id', 'generation_id', 'name', 'slug', 'manufacturer', 'release_year',
                  'sort_order', 'active']


class RetroGameSerializer(serializers.ModelSerializer):
    activity_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(source='activity.name', read_only=True)
    description = serializers.CharField(source='activity.description', read_only=True, allow_null=True)
    default_block_minutes = serializers.IntegerField(source='activity.duration', read_only=True)
    generation = serializers.SerializerMethodField()
    platform = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    progress_version = serializers.SerializerMethodField()
    played_seconds = serializers.SerializerMethodField()
    open_estimate_seconds = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    coverage = serializers.SerializerMethodField()
    can_start = serializers.SerializerMethodField()

    class Meta:
        model = RetroGame
        fields = ['id', 'activity_id', 'name', 'description', 'default_block_minutes',
                  'generation', 'platform', 'tier', 'estimated_main_minutes', 'play_goal',
                  'release_year', 'sort_order', 'active', 'cover_url', 'status',
                  'progress_version', 'played_seconds', 'open_estimate_seconds',
                  'progress_percent', 'coverage', 'can_start']

    def _metrics(self, obj):
        return self.context.get('metrics', {}).get(obj.id, {})

    def get_generation(self, obj):
        generation = obj.platform.generation
        return {'id': generation.id, 'name': generation.name,
                'sort_order': generation.retro_sort_order}

    def get_platform(self, obj):
        platform = obj.platform
        return {'id': platform.id, 'name': platform.name, 'slug': platform.slug}

    def get_status(self, obj):
        progress = self._metrics(obj).get('progress')
        if progress:
            return progress.status
        if self.get_played_seconds(obj) > 0 or self.get_open_estimate_seconds(obj) > 0:
            return 'in_progress'
        return 'not_started'

    def get_progress_version(self, obj):
        progress = self._metrics(obj).get('progress')
        return progress.version if progress else 0

    def get_played_seconds(self, obj):
        return self._metrics(obj).get('played_seconds', 0)

    def get_open_estimate_seconds(self, obj):
        return self._metrics(obj).get('open_estimate_seconds', 0)

    def get_progress_percent(self, obj):
        seconds = self.get_played_seconds(obj)
        return round(seconds * 100 / (obj.estimated_main_minutes * 60), 2)

    def get_coverage(self, obj):
        return self._metrics(obj).get('coverage', 'complete')

    def get_can_start(self, obj):
        from django.conf import settings
        return bool(getattr(settings, 'RETROGAMES_ENABLED', False) and obj.active
                    and obj.platform.active and obj.activity.active
                    and obj.platform.generation.group.is_retro_catalog
                    and obj.activity.category_id == obj.platform.generation_id)


class RetroProgressRequestSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=RetroGameProgress.STATUS_CHOICES)
    expected_version = serializers.IntegerField(min_value=0)


class RetroProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetroGameProgress
        fields = ['status', 'started_at', 'completed_at', 'version']


class QueueRecreationRequestSerializer(serializers.Serializer):
    group_id = serializers.IntegerField(required=False, min_value=1)
    expected_queue_id = serializers.IntegerField(min_value=1)


class QueueRecreationResponseSerializer(serializers.Serializer):
    queue_id = serializers.IntegerField()
    recreated_from_queue_id = serializers.IntegerField()
    queue_group_id = serializers.IntegerField()
    queue_group_name = serializers.CharField()
    queue_mode = serializers.CharField()
    pool_number = serializers.IntegerField()
    pool_size = serializers.IntegerField()
    skip_locked = serializers.BooleanField()
    requeued_skipped_count = serializers.IntegerField()
    skipped_not_requeued = serializers.ListField(child=serializers.DictField())


class QueueActivityCategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class QueueActivitySummarySerializer(serializers.Serializer):
    queue_item_id = serializers.IntegerField()
    position = serializers.IntegerField()
    state = serializers.CharField()
    activity_id = serializers.IntegerField()
    name = serializers.CharField()
    category = QueueActivityCategorySerializer()
    execution_count = serializers.IntegerField()
    last_execution_at = serializers.DateTimeField(
        allow_null=True,
        default_timezone=datetime_timezone.utc,
    )
    skip_count = serializers.IntegerField()


class ActiveQueueSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    group_id = serializers.IntegerField()
    group_name = serializers.CharField()
    mode = serializers.CharField()
    pool_number = serializers.IntegerField()
    pool_size = serializers.IntegerField()
    skip_locked = serializers.BooleanField()


class StrictGoalSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({key: 'Campo não permitido.' for key in sorted(unknown)})
        return super().to_internal_value(data)


class WeeklyGoalCreateSerializer(StrictGoalSerializer):
    metric = serializers.ChoiceField(choices=['minutes', 'sessions'])
    group_id = serializers.PrimaryKeyRelatedField(source='group', queryset=Group.objects.all(), required=False)
    category_id = serializers.PrimaryKeyRelatedField(source='category', queryset=Category.objects.all(), required=False)
    target = serializers.IntegerField(min_value=1, max_value=2147483647)

    def validate(self, attrs):
        if ('group' in attrs) == ('category' in attrs):
            raise serializers.ValidationError('Informe exatamente um destino: group_id ou category_id.')
        return attrs


class WeeklyGoalUpdateSerializer(StrictGoalSerializer):
    target = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    active = serializers.BooleanField(required=False)
    expected_version = serializers.IntegerField(min_value=1, max_value=2147483647)

    def validate(self, attrs):
        if not {'target', 'active'} & attrs.keys():
            raise serializers.ValidationError('Informe target ou active para editar a meta.')
        return attrs


class WeeklyGoalSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    metric = serializers.CharField()
    group_id = serializers.IntegerField(allow_null=True)
    category_id = serializers.IntegerField(allow_null=True)
    is_all_groups = serializers.BooleanField()
    target = serializers.IntegerField()
    active = serializers.BooleanField()
    effective_week = serializers.DateField()
    version = serializers.IntegerField()


class QueueActivityListResponseSerializer(serializers.Serializer):
    queue = ActiveQueueSummarySerializer(allow_null=True)
    returned_count = serializers.IntegerField()
    available_count = serializers.IntegerField()
    has_more = serializers.BooleanField()
    statistics_scope = serializers.CharField()
    activities = QueueActivitySummarySerializer(many=True)
