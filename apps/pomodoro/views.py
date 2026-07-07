from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from .models import Activity, ActivityQueueItem, Group, History, Schedule
from .serializers import (
    ActivityExecutionSerializer,
    ActivityQueueItemSerializer,
    ActivitySerializer,
    GroupSerializer,
    HistorySerializer,
)
from .services.activity_execution import (
    ActivityExecutionConflict,
    build_scope_key,
    complete_schedule,
    get_active_schedule,
    reconcile_schedule,
    start_activity,
)
from .services.activity_queue import (
    QueueConflict,
    expire_finished_premiums,
    get_requested_group,
    present_next_item,
    skip_item,
)


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = GroupSerializer
    queryset = Group.objects.all()


class ActivityViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = ActivitySerializer
    queryset = Activity.objects.filter(active=True).select_related('category', 'category__group')

    def get_queryset(self):
        expire_finished_premiums()
        queryset = super().get_queryset()
        category_id = self.request.query_params.get('category_id')
        group = get_requested_group(self.request)

        if category_id:
            queryset = queryset.filter(category_id=category_id)

        if group and not group.is_default:
            queryset = queryset.filter(category__group=group)

        return queryset.order_by('-premium', 'name')

    @action(detail=False, methods=['get'])
    def next(self, request):
        item = present_next_item(
            scope_key=build_scope_key(request),
            selected_group=get_requested_group(request),
        )
        if not item:
            return Response(
                {"detail": "Nenhuma atividade disponivel"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ActivityQueueItemSerializer(item, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        activity = self.get_object()
        queue_item_id = request.data.get('queue_item_id')
        if not queue_item_id:
            return Response(
                {
                    "code": "queue_item_required",
                    "detail": "O campo queue_item_id e obrigatorio para iniciar uma atividade da fila.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            queue_item = ActivityQueueItem.objects.select_related('queue').get(pk=queue_item_id)
        except ActivityQueueItem.DoesNotExist:
            return Response(
                {"code": "queue_item_not_found", "detail": "Item da fila nao encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            schedule, created = start_activity(
                activity=activity,
                queue_item=queue_item,
                scope_key=build_scope_key(request),
            )
        except ActivityExecutionConflict as exc:
            payload = {
                "code": exc.code,
                "detail": exc.detail,
            }
            if exc.schedule:
                payload["active_execution"] = ActivityExecutionSerializer(
                    exc.schedule,
                    context={'request': request},
                ).data
            return Response(payload, status=status.HTTP_409_CONFLICT)

        response_data = ActivityExecutionSerializer(schedule, context={'request': request}).data
        response_data['schedule_id'] = schedule.id
        response_data['activity_id'] = activity.id
        response_data['date'] = schedule.scheduled_date.isoformat()
        response_data['start_time'] = schedule.start_time.strftime("%H:%M:%S")
        response_data['status'] = 'started' if created else 'already_started'
        return Response(
            response_data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'])
    def complete(self, request):
        schedule_id = request.data.get('schedule_id')
        if not schedule_id:
            return Response(
                {"error": "O campo schedule_id e obrigatorio"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            schedule = Schedule.objects.get(pk=schedule_id, scope_key=build_scope_key(request))
        except Schedule.DoesNotExist:
            return Response(
                {"error": "Agendamento nao encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        schedule = complete_schedule(schedule)
        response_data = ActivityExecutionSerializer(schedule, context={'request': request}).data
        response_data['schedule_id'] = schedule.id
        response_data['status'] = 'completed'
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def active(self, request):
        schedule = get_active_schedule(build_scope_key(request))
        if not schedule:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data)

    @action(detail=False, methods=['get'])
    def history(self, request):
        history_entries = (
            History.objects.select_related('activity__category__group')
            .order_by('-start_time')
        )
        if not history_entries.exists():
            return Response(
                {
                    "detail": "Nenhum registro de historico encontrado",
                    "suggestion": "Execute atividades para gerar historico",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(HistorySerializer(history_entries, many=True).data)

    @action(detail=False, methods=['get'], url_path=r'status/(?P<schedule_id>[^/.]+)')
    def status(self, request, schedule_id=None):
        try:
            schedule = Schedule.objects.select_related(
                'activity__category__group',
                'queue_item__queue',
            ).get(pk=schedule_id, scope_key=build_scope_key(request))
        except Schedule.DoesNotExist:
            return Response(
                {"error": "Schedule nao encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )
        schedule = reconcile_schedule(schedule)
        response_data = ActivityExecutionSerializer(schedule, context={'request': request}).data
        response_data['schedule_id'] = schedule.id
        return Response(response_data)


class ActivityQueueItemViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    queryset = ActivityQueueItem.objects.select_related('queue', 'activity__category__group')

    @action(detail=True, methods=['post'])
    def skip(self, request, pk=None):
        try:
            item = skip_item(
                queue_item_id=int(pk),
                scope_key=build_scope_key(request),
            )
        except ActivityQueueItem.DoesNotExist:
            return Response(
                {"code": "queue_item_not_found", "detail": "Item da fila nao encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except QueueConflict as exc:
            return Response(
                {"code": exc.code, "detail": exc.detail},
                status=status.HTTP_409_CONFLICT,
            )

        payload = {
            "queue_item_id": item.id,
            "activity_id": item.activity_id,
            "state": item.state,
            "next_queue_item_id": (
                item.queue.items.filter(state=ActivityQueueItem.STATE_PENDING)
                .order_by('position')
                .values_list('id', flat=True)
                .first()
            ),
        }
        return Response(payload, status=status.HTTP_200_OK)


class ActivityExecutionViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    queryset = Schedule.objects.select_related('activity__category__group', 'queue_item__queue')

    def retrieve(self, request, pk=None):
        try:
            schedule = self.get_queryset().get(pk=pk, scope_key=build_scope_key(request))
        except Schedule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        schedule = reconcile_schedule(schedule)
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def reconcile(self, request, pk=None):
        try:
            schedule = self.get_queryset().get(pk=pk, scope_key=build_scope_key(request))
        except Schedule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        schedule = reconcile_schedule(schedule)
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data)
