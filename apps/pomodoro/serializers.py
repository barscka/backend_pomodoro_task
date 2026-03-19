# apps/pomodoro/serializers.py
from rest_framework import serializers
from .models import Activity, Category, Group, History, Schedule


class GroupSerializer(serializers.ModelSerializer):
    current_executions = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'color', 'max_daily_executions', 'is_default', 'current_executions']

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
    
    class Meta:
        model = Activity
        fields = ['id', 'name', 'description', 'duration', 'category', 
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
