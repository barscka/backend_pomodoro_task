# apps/pomodoro/serializers.py
from rest_framework import serializers
from .models import Activity, Category,  History, Schedule

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'color']

class ActivitySerializer(serializers.ModelSerializer):
    can_execute = serializers.SerializerMethodField()
    remaining_executions = serializers.SerializerMethodField()
    
    class Meta:
        model = Activity
        fields = ['id', 'name', 'description', 'duration', 'category', 
                 'created_at', 'last_executed', 'executions_today',
                 'can_execute', 'remaining_executions']
    
    def get_can_execute(self, obj):
        return obj.category.can_execute_more() if obj.category else False
    
    def get_remaining_executions(self, obj):
        if obj.category:
            return obj.category.max_daily_executions - obj.category.current_executions
        return None

# apps/pomodoro/serializers.py
class HistorySerializer(serializers.ModelSerializer):
    activity_name = serializers.CharField(source='activity.name', read_only=True)
    category_name = serializers.CharField(source='activity.category.name', read_only=True)
    completed = serializers.SerializerMethodField()

    class Meta:
        model = History
        fields = [
            'id',
            'activity_name',
            'category_name',
            'start_time',
            'end_time',
            'duration',
            'completed'
        ]

    def get_completed(self, obj):
        """Determina se a atividade foi completada baseado no end_time"""
        return obj.end_time is not None