import logging

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from .models import Activity, ActivityQueueItem, Group, History, Schedule
from .serializers import (
    QueueActivityListResponseSerializer,
    QueueRecreationRequestSerializer,
    QueueRecreationResponseSerializer,
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
    list_active_queue_activities,
    present_next_item,
    recreate_active_queue,
    skip_item,
)
from .services.activity_queue_reconciliation import activity_snapshot, reconcile_activity

logger = logging.getLogger(__name__)


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = GroupSerializer
    queryset = Group.objects.all()


class ActivityViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = ActivitySerializer
    queryset = Activity.objects.all().select_related('category', 'category__group')

    def get_queryset(self):
        expire_finished_premiums()
        queryset = super().get_queryset()
        if self.action in ['list', 'next']:
            queryset = queryset.filter(active=True)
        category_id = self.request.query_params.get('category_id')
        group = get_requested_group(self.request)

        if category_id:
            queryset = queryset.filter(category_id=category_id)

        if group and not group.is_default:
            queryset = queryset.filter(category__group=group)

        return queryset.order_by('-premium', 'name')

    def perform_create(self, serializer):
        with transaction.atomic():
            activity = serializer.save()
            reconcile_activity(activity)

    def perform_update(self, serializer):
        with transaction.atomic():
            previous = activity_snapshot(self.get_object())
            activity = serializer.save()
            reconcile_activity(activity, previous=previous)

    @action(detail=False, methods=['get'])
    def next(self, request):
        result = present_next_item(
            scope_key=build_scope_key(request),
            selected_group=get_requested_group(request),
        )
        if not result.item:
            return Response(
                {
                    "code": "no_activity_available",
                    "detail": "Nenhuma atividade disponivel para este grupo.",
                    "reason": result.reason or "unknown",
                    "queue_group_id": result.group.id,
                    "queue_group_name": result.group.name,
                    "group_max_daily_minutes": result.group.max_daily_minutes,
                    "group_consumed_daily_minutes": result.consumed_daily_minutes,
                    "group_remaining_daily_minutes": result.remaining_daily_minutes,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ActivityQueueItemSerializer(
            result.item,
            context={
                'request': request,
                'group_daily_metrics': {
                    'group_max_daily_minutes': result.group.max_daily_minutes,
                    'group_consumed_daily_minutes': result.consumed_daily_minutes,
                    'group_remaining_daily_minutes': result.remaining_daily_minutes,
                },
            },
        ).data)

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
                **exc.payload,
            }
            if exc.schedule:
                payload["active_execution"] = ActivityExecutionSerializer(
                    exc.schedule,
                    context={'request': request},
                ).data
            return Response(payload, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception(
                'Unexpected failure while starting activity',
                extra={'activity_id': activity.id, 'queue_item_id': queue_item.id},
            )
            return Response(
                {
                    "code": "activity_start_failed",
                    "detail": "Nao foi possivel iniciar a atividade no backend.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
            schedule = Schedule.objects.select_related(
                'activity__category__group',
                'queue_item__queue__group',
            ).get(pk=schedule_id, scope_key=build_scope_key(request))
        except Schedule.DoesNotExist:
            return Response(
                {"error": "Agendamento nao encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )

        schedule = complete_schedule(schedule)
        schedule = Schedule.objects.select_related(
            'activity__category__group',
            'queue_item__queue__group',
        ).get(pk=schedule.pk)
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
                'queue_item__queue__group',
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
                {"code": exc.code, "detail": exc.detail, **exc.payload},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception(
                'Unexpected failure while skipping queue item',
                extra={'queue_item_id': pk},
            )
            return Response(
                {
                    "code": "queue_item_skip_failed",
                    "detail": "Nao foi possivel pular a atividade no backend.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        payload = {
            "queue_item_id": item.id,
            "activity_id": item.activity_id,
            "state": item.state,
            "next_queue_item_id": (
                ActivityQueueItem.objects.filter(
                    queue__scope_key=item.queue.scope_key,
                    queue__group=item.queue.group,
                    queue__state='active',
                    state=ActivityQueueItem.STATE_PENDING,
                )
                .order_by('position')
                .values_list('id', flat=True)
                .first()
            ),
        }
        return Response(payload, status=status.HTTP_200_OK)


class ActivityQueueViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    PREVIEW_LIMIT = 30

    @staticmethod
    def _group(group_id):
        if group_id is None:
            return None
        return Group.objects.filter(pk=group_id).first()

    @action(detail=False, methods=['post'])
    def recreate(self, request):
        if request.data.get('expected_queue_id') in [None, '']:
            return Response(
                {
                    'code': 'expected_queue_id_required',
                    'detail': 'O campo expected_queue_id e obrigatorio.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = QueueRecreationRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'code': 'invalid_request',
                    'detail': 'Os dados informados sao invalidos.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        group_id = serializer.validated_data.get('group_id')
        group = self._group(group_id)
        if group_id is not None and group is None:
            return Response(
                {'code': 'group_not_found', 'detail': 'Grupo nao encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            result = recreate_active_queue(
                scope_key=build_scope_key(request),
                selected_group=group,
                expected_queue_id=serializer.validated_data['expected_queue_id'],
            )
        except QueueConflict as exc:
            response_status = (
                status.HTTP_404_NOT_FOUND
                if exc.code == 'active_queue_not_found'
                else status.HTTP_409_CONFLICT
            )
            return Response(
                {'code': exc.code, 'detail': exc.detail, **exc.payload},
                status=response_status,
            )
        except Exception:
            logger.exception(
                'Unexpected failure while recreating activity queue',
                extra={'group_id': group_id},
            )
            return Response(
                {
                    'code': 'queue_recreation_failed',
                    'detail': 'Nao foi possivel recriar a fila.',
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        queue = result.queue
        payload = {
            'queue_id': queue.id,
            'recreated_from_queue_id': result.recreated_from_queue_id,
            'queue_group_id': queue.group_id,
            'queue_group_name': queue.group.name,
            'queue_mode': queue.mode,
            'pool_number': queue.pool_number,
            'pool_size': queue.pool_size,
            'skip_locked': queue.skip_locked,
            'requeued_skipped_count': result.requeued_skipped_count,
            'skipped_not_requeued': result.skipped_not_requeued,
        }
        return Response(
            QueueRecreationResponseSerializer(payload).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['get'])
    def activities(self, request):
        raw_group_id = request.query_params.get('group_id')
        try:
            group_id = int(raw_group_id) if raw_group_id not in [None, ''] else None
        except (TypeError, ValueError):
            return Response(
                {
                    'code': 'invalid_request',
                    'detail': 'O group_id informado e invalido.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        group = self._group(group_id)
        if group_id is not None and group is None:
            return Response(
                {'code': 'group_not_found', 'detail': 'Grupo nao encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        result = list_active_queue_activities(
            scope_key=build_scope_key(request),
            selected_group=group,
            limit=self.PREVIEW_LIMIT,
        )
        queue = result.queue
        payload = {
            'queue': None if queue is None else {
                'id': queue.id,
                'group_id': queue.group_id,
                'group_name': queue.group.name,
                'mode': queue.mode,
                'pool_number': queue.pool_number,
                'pool_size': queue.pool_size,
                'skip_locked': queue.skip_locked,
            },
            'returned_count': len(result.activities),
            'available_count': result.available_count,
            'has_more': result.available_count > len(result.activities),
            'statistics_scope': 'all_time',
            'activities': result.activities,
        }
        return Response(QueueActivityListResponseSerializer(payload).data)


class ActivityExecutionViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    queryset = Schedule.objects.select_related(
        'activity__category__group',
        'queue_item__queue__group',
    )

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
