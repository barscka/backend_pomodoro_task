# apps/pomodoro/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import ActivityExecutionViewSet, ActivityQueueItemViewSet, ActivityViewSet, GroupViewSet

router = DefaultRouter()
router.register(r'groups', GroupViewSet, basename='group')
router.register(r'activities', ActivityViewSet, basename='activity')
router.register(r'activity-queue/items', ActivityQueueItemViewSet, basename='activity-queue-item')
router.register(r'activity-executions', ActivityExecutionViewSet, basename='activity-execution')
urlpatterns = [
    path('activities/history/', ActivityViewSet.as_view({'get': 'history'}), name='activity-history'),
    path('activities/active/', ActivityViewSet.as_view({'get': 'active'}), name='activity-active'),
] + router.urls
