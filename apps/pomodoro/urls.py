# apps/pomodoro/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import ActivityViewSet, GroupViewSet

router = DefaultRouter()
router.register(r'groups', GroupViewSet, basename='group')
router.register(r'activities', ActivityViewSet, basename='activity')
urlpatterns = [
    path('activities/history/', ActivityViewSet.as_view({'get': 'history'}), name='activity-history'),
] + router.urls
