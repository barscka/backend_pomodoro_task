from datetime import timedelta

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from apps.pomodoro.repositories.weekly_goals import goals_for_week, pending_reconciliations
from apps.pomodoro.serializers import (
    WeeklyGoalCreateSerializer, WeeklyGoalSerializer, WeeklyGoalUpdateSerializer,
)
from apps.pomodoro.services.activity_execution import build_scope_key
from apps.pomodoro.services.weekly_goals import (
    GOAL_TIMEZONE, GoalError, create_goal, current_week, parse_week, progress_rows,
    update_goal, week_bounds,
)


class GoalPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class WeeklyGoalViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    pagination_class = GoalPagination
    serializer_class = WeeklyGoalSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    lookup_value_regex = '[0-9]+'

    def handle_exception(self, exc):
        if isinstance(exc, GoalError):
            return Response({'code': exc.code, 'detail': exc.detail}, status=exc.status)
        return super().handle_exception(exc)

    def _validated(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        if not serializer.is_valid():
            return None, Response({
                'code': 'invalid_goal', 'detail': 'Dados da meta inválidos.', 'fields': serializer.errors,
            }, status=400)
        return serializer.validated_data, None

    def list(self, request):
        week = current_week(timezone.now())
        queryset = goals_for_week(build_scope_key(request), week)
        active = request.query_params.get('active')
        if active is not None:
            if active not in ['true', 'false']:
                raise GoalError('invalid_goal', 'active deve ser true ou false.')
            queryset = queryset.filter(active=active == 'true')
        return self.get_paginated_response(self.get_serializer(self.paginate_queryset(queryset), many=True).data)

    def retrieve(self, request, pk=None):
        goal = goals_for_week(build_scope_key(request), current_week(timezone.now())).filter(pk=pk).first()
        if goal is None:
            raise GoalError('goal_not_found', 'Meta não encontrada.', 404)
        return Response(self.get_serializer(goal).data)

    def create(self, request):
        week = current_week(timezone.now())
        data, error = self._validated(WeeklyGoalCreateSerializer)
        if error is not None:
            return error
        goal = create_goal(scope_key=build_scope_key(request), data=data, week=week)
        return Response(self.get_serializer(goal).data, status=201)

    def partial_update(self, request, pk=None):
        week = current_week(timezone.now())
        data, error = self._validated(WeeklyGoalUpdateSerializer)
        if error is not None:
            return error
        goal = update_goal(scope_key=build_scope_key(request), goal_id=pk, data=data, week=week)
        return Response(self.get_serializer(goal).data)

    @action(detail=False, methods=['get'])
    def progress(self, request):
        now = timezone.now()
        scope = build_scope_key(request)
        week = parse_week(request.query_params.get('week_start'), now)
        start, end = week_bounds(week)
        goals = self.paginate_queryset(goals_for_week(scope, week).filter(active=True))
        response = self.get_paginated_response(progress_rows(
            goals, scope_key=scope, start=start, end=end, as_of=now,
        ))
        response.data = {
            'week_start': week.isoformat(),
            'week_end_exclusive': (week + timedelta(days=7)).isoformat(),
            'timezone': str(GOAL_TIMEZONE), 'as_of': now,
            'pending_reconciliation_count': pending_reconciliations(scope, start, end, now),
            **response.data,
        }
        return response
