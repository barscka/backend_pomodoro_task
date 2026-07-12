# apps/pomodoro/serializers.py
from rest_framework import serializers
from .models import (
    Activity,
    ActivityQueueItem,
    Category,
    Group,
    History,
    Schedule,
)
from .services.activity_queue import group_daily_metrics


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'color', 'is_default', 'max_daily_minutes']

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
