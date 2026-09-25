# apps/pomodoro/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path
from .weekly_goal_views import WeeklyGoalViewSet
from .views import (
    ActivityExecutionViewSet,
    ActivityQueueItemViewSet,
    ActivityQueueViewSet,
    ActivityViewSet,
    CategoryViewSet,
    GroupViewSet,
    PremiumPeriodViewSet,
    PremiumAnalyticsViewSet,
    GameplayTrackingSettingsViewSet,
    RetroGameViewSet,
    RetroGenerationViewSet,
    RetroPlatformViewSet,
)

router = DefaultRouter()
router.register(r'weekly-goals', WeeklyGoalViewSet, basename='weekly-goal')
router.register(r'groups', GroupViewSet, basename='group')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'activities', ActivityViewSet, basename='activity')
router.register(r'activity-queue/items', ActivityQueueItemViewSet, basename='activity-queue-item')
router.register(r'activity-queue', ActivityQueueViewSet, basename='activity-queue')
router.register(r'activity-executions', ActivityExecutionViewSet, basename='activity-execution')
router.register(r'premium-periods', PremiumPeriodViewSet, basename='premium-period')
router.register(r'premium-analytics', PremiumAnalyticsViewSet, basename='premium-analytics')
router.register(r'retro-generations', RetroGenerationViewSet, basename='retro-generation')
router.register(r'retro-platforms', RetroPlatformViewSet, basename='retro-platform')
router.register(r'retro-games', RetroGameViewSet, basename='retro-game')
urlpatterns = [
    path('activities/history/', ActivityViewSet.as_view({'get': 'history'}), name='activity-history'),
    path('activities/active/', ActivityViewSet.as_view({'get': 'active'}), name='activity-active'),
    path('gameplay-tracking-settings/', GameplayTrackingSettingsViewSet.as_view({'get': 'list', 'patch': 'partial_update'}), name='gameplay-tracking-settings'),
] + router.urls
