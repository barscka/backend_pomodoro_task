import logging

from django.db import models, transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from .models import (Activity, ActivityQueueItem, Category, Group, History, Schedule,
                     PremiumPeriod, GameplayTrackingSettings, GoalCompletion)
from .serializers import (
    QueueActivityListResponseSerializer,
    QueueRecreationRequestSerializer,
    QueueRecreationResponseSerializer,
    ActivityExecutionSerializer,
    ActivityQueueItemSerializer,
    ActivitySerializer,
    CategorySerializer,
    GroupSerializer,
    HistorySerializer,
    PremiumPeriodSerializer, PremiumStartSerializer, PremiumContinueSerializer,
    RetroGameSerializer, RetroGenerationSerializer, RetroPlatformSerializer,
    RetroProgressRequestSerializer, RetroProgressSerializer,
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


class PremiumPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class RetroPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class RetroGenerationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = RetroGenerationSerializer

    def get_queryset(self):
        from .services.retro_catalog import generations
        return generations()


class RetroPlatformViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = RetroPlatformSerializer

    def get_queryset(self):
        from .services.retro_catalog import platforms
        return platforms(generation_id=self.request.query_params.get('generation_id'))


class RetroGameViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = RetroGameSerializer
    pagination_class = RetroPagination

    def get_queryset(self):
        from .services.retro_catalog import games
        return games(scope_key=build_scope_key(self.request), params=self.request.query_params)

    def _serialized(self, objects, many=False):
        from .services.retro_catalog import metrics_for
        object_list = list(objects) if many else [objects]
        metrics = metrics_for(game_ids=[game.id for game in object_list],
                              scope_key=build_scope_key(self.request))
        value = object_list if many else object_list[0]
        return self.get_serializer(value, many=many, context={
            **self.get_serializer_context(), 'metrics': metrics,
        }).data

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        if page is not None:
            return self.get_paginated_response(self._serialized(page, many=True))
        return Response(self._serialized(self.get_queryset(), many=True))

    def retrieve(self, request, *args, **kwargs):
        return Response(self._serialized(self.get_object()))

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        serializer = PremiumStartSerializer(data=request.data)
        if not serializer.is_valid():
            code = 'invalid_duration' if 'duration_minutes' in serializer.errors else 'invalid_request'
            return Response({'code': code, 'detail': 'Dados de início inválidos.',
                             'errors': serializer.errors}, status=400)
        try:
            from .services.retro_execution import start_retro
            schedule, created = start_retro(
                retro_game_id=pk, scope_key=build_scope_key(request),
                **serializer.validated_data,
            )
        except ActivityExecutionConflict as exc:
            status_by_code = {
                'retro_game_not_found': status.HTTP_404_NOT_FOUND,
                'retro_game_inactive': status.HTTP_422_UNPROCESSABLE_ENTITY,
                'retrogames_disabled': status.HTTP_503_SERVICE_UNAVAILABLE,
                'retro_hierarchy_invalid': status.HTTP_400_BAD_REQUEST,
                'invalid_duration': status.HTTP_400_BAD_REQUEST,
                'return_group_not_found': status.HTTP_400_BAD_REQUEST,
            }
            payload = {'code': exc.code, 'detail': exc.detail, **exc.payload}
            if exc.schedule:
                payload['active_execution'] = ActivityExecutionSerializer(
                    exc.schedule, context={'request': request}
                ).data
            return Response(payload, status=status_by_code.get(exc.code, status.HTTP_409_CONFLICT))
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data,
                        status=201 if created else 200)

    @action(detail=True, methods=['patch'])
    def progress(self, request, pk=None):
        serializer = RetroProgressRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game = self.get_object()
        try:
            from .services.retro_progress import RetroProgressConflict, set_progress
            progress, _ = set_progress(retro_game=game, scope_key=build_scope_key(request),
                                       **serializer.validated_data)
        except RetroProgressConflict as exc:
            return Response({'code': exc.code, 'detail': exc.detail}, status=409)
        return Response(RetroProgressSerializer(progress).data)

    @action(detail=True, methods=['get'])
    def sessions(self, request, pk=None):
        game = self.get_object()
        queryset = GoalCompletion.objects.filter(
            scope_key=build_scope_key(request), activity_id_snapshot=game.activity_id
        ).order_by('-completed_at', '-source_schedule_id')
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        data = [{
            'execution_id': fact.source_schedule_id,
            'started_at': fact.started_at,
            'completed_at': fact.completed_at,
            'duration_seconds': (fact.duration_seconds if fact.duration_seconds is not None
                                 else fact.duration_minutes * 60),
            'coverage': 'complete' if fact.duration_seconds is not None else 'partial',
            'execution_origin': fact.execution_origin,
        } for fact in rows]
        return self.get_paginated_response(data) if page is not None else Response(data)


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = GroupSerializer
    queryset = Group.objects.all()


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = CategorySerializer
    queryset = Category.objects.select_related('group').order_by('name')


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
            if activity.premium and not activity.premium_periods.exists():
                from .services.premium_periods import create_period
                create_period(activity_id=activity.id, kind='focus', title=activity.name,
                              starts_on=activity.premium_from, ends_on=activity.premium_until,
                              source='legacy_compat')
            reconcile_activity(activity)

    def perform_update(self, serializer):
        with transaction.atomic():
            previous = activity_snapshot(self.get_object())
            activity = serializer.save()
            if activity.premium and not activity.premium_periods.exists():
                from .services.premium_periods import create_period
                create_period(activity_id=activity.id, kind='focus', title=activity.name,
                              starts_on=activity.premium_from, ends_on=activity.premium_until,
                              source='legacy_compat')
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

        try:
            schedule = complete_schedule(schedule)
        except ActivityExecutionConflict as exc:
            return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)
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
        'retro_game',
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

    @action(detail=True, methods=['post'], url_path='continue')
    def continue_(self, request, pk=None):
        serializer = PremiumContinueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            predecessor = self.get_queryset().get(pk=pk, scope_key=build_scope_key(request))
            if predecessor.execution_origin == Schedule.ORIGIN_RETRO_DIRECT and predecessor.retro_game_id:
                from .services.retro_execution import start_retro
                schedule, created = start_retro(
                    retro_game_id=predecessor.retro_game_id,
                    scope_key=build_scope_key(request), continued_from_id=predecessor.id,
                    **serializer.validated_data,
                )
            elif predecessor.premium_period_id:
                from .services.premium_execution import start_premium
                schedule, created = start_premium(
                    period_id=predecessor.premium_period_id, scope_key=build_scope_key(request),
                    continued_from_id=predecessor.id, **serializer.validated_data)
            else:
                return Response({'code': 'continuation_not_available', 'detail': 'A execução não possui contexto de continuação.'}, status=409)
        except Schedule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ActivityExecutionConflict as exc:
            payload = {'code': exc.code, 'detail': exc.detail}
            if exc.schedule:
                payload['active_execution'] = ActivityExecutionSerializer(exc.schedule, context={'request': request}).data
            response_status = (status.HTTP_503_SERVICE_UNAVAILABLE
                               if exc.code == 'retrogames_disabled'
                               else status.HTTP_409_CONFLICT)
            return Response(payload, status=response_status)
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PremiumPeriodViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = PremiumPeriodSerializer
    queryset = PremiumPeriod.objects.select_related('activity__category__group')
    pagination_class = PremiumPagination
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('activity_id'):
            queryset = queryset.filter(activity_id=self.request.query_params['activity_id'])
        requested_status = self.request.query_params.get('status')
        if requested_status:
            now = timezone.now()
            today = timezone.localdate(now)
            if requested_status == 'future':
                queryset = queryset.filter(starts_on__gt=today, ended_early_at__isnull=True)
            elif requested_status == 'active':
                queryset = queryset.filter(starts_on__lte=today, ends_on__gte=today, ended_early_at__isnull=True)
            elif requested_status == 'ended':
                queryset = queryset.filter(models.Q(ends_on__lt=today) | models.Q(ended_early_at__isnull=False))
        return queryset

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .services.premium_periods import create_period, PremiumPeriodConflict
        try:
            period = create_period(activity_id=serializer.validated_data.pop('activity').id, **serializer.validated_data)
        except PremiumPeriodConflict as exc:
            return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(period).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        from .services.premium_periods import validate_no_overlap, sync_activity_premium_projection, PremiumPeriodConflict
        with transaction.atomic():
            period = self.get_queryset().select_for_update().get(pk=pk)
            expected = request.data.get('expected_version')
            if expected is None or int(expected) != period.version:
                return Response({'code': 'stale_period_version', 'detail': 'Versão desatualizada.'}, status=409)
            if period.schedules.exists() or timezone.now() >= __import__('apps.pomodoro.services.premium_periods', fromlist=['bounds']).bounds(period)[0]:
                return Response({'code': 'premium_period_edit_locked', 'detail': 'Período iniciado ou utilizado não pode ser alterado.'}, status=409)
            serializer = self.get_serializer(period, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            starts = serializer.validated_data.get('starts_on', period.starts_on)
            ends = serializer.validated_data.get('ends_on', period.ends_on)
            try:
                validate_no_overlap(period.activity, starts, ends, exclude_id=period.id)
            except PremiumPeriodConflict as exc:
                return Response({'code': exc.code, 'detail': exc.detail}, status=409)
            period = serializer.save(version=period.version + 1)
            sync_activity_premium_projection(period.activity)
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        period = self.get_object()
        if not period.ended_early_at:
            from .services.premium_periods import bounds
            now = timezone.now()
            if now < bounds(period)[0]:
                return Response({'code': 'premium_period_not_started', 'detail': 'Um período futuro não pode ser encerrado antecipadamente.'}, status=409)
            period.ended_early_at = now
            period.version += 1
            period.save(update_fields=['ended_early_at', 'version', 'updated_at'])
            from .services.premium_periods import sync_activity_premium_projection
            sync_activity_premium_projection(period.activity)
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        serializer = PremiumStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            from .services.premium_execution import start_premium
            schedule, created = start_premium(period_id=pk, scope_key=build_scope_key(request), **serializer.validated_data)
        except ActivityExecutionConflict as exc:
            payload = {'code': exc.code, 'detail': exc.detail}
            if exc.schedule:
                payload['active_execution'] = ActivityExecutionSerializer(exc.schedule, context={'request': request}).data
            return Response(payload, status=409)
        return Response(ActivityExecutionSerializer(schedule, context={'request': request}).data,
                        status=201 if created else 200)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        from .services.premium_reporting import period_stats
        return Response(period_stats(self.get_object(), build_scope_key(request)))


class PremiumAnalyticsViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]

    def _dates(self, request):
        from datetime import timedelta
        today = timezone.localdate()
        try:
            start = timezone.datetime.fromisoformat(request.query_params.get('date_from', str(today - timedelta(days=29)))).date()
            end = timezone.datetime.fromisoformat(request.query_params.get('date_to', str(today))).date()
        except ValueError:
            return None
        if start > end or (end - start).days > 365:
            return None
        return start, end

    @action(detail=False, methods=['get'])
    def daily(self, request):
        dates = self._dates(request)
        if not dates:
            return Response({'code': 'invalid_date_range'}, status=400)
        from .services.premium_reporting import daily_summary
        values, config = daily_summary(build_scope_key(request), *dates)
        return Response({'date_from': dates[0], 'date_to': dates[1], 'as_of': timezone.now(), **config, 'results': values})

    @action(detail=False, methods=['get'])
    def summary(self, request):
        response = self.daily(request)
        if response.status_code != 200:
            return response
        rows = response.data.pop('results')
        response.data['premium_seconds'] = sum(row['premium_seconds'] for row in rows)
        response.data['gameplay_seconds'] = sum(row['gameplay_seconds'] for row in rows)
        response.data['other_gameplay_seconds'] = sum(row['other_gameplay_seconds'] for row in rows)
        response.data['reference_seconds'] = ((response.data['date_to'] - response.data['date_from']).days + 1) * response.data['daily_reference_minutes'] * 60
        from collections import defaultdict
        from .services.premium_reporting import report_window, segments
        start, end = report_window(response.data['date_from'], response.data['date_to'])
        distribution = defaultdict(lambda: {'seconds': 0, 'session_ids': set()})
        for fact, period, _segment_start, _segment_end, seconds in segments(build_scope_key(request), start, end):
            key = (period.activity_id, period.activity.name)
            distribution[key]['seconds'] += seconds
            distribution[key]['session_ids'].add(fact.source_schedule_id)
        response.data['distribution'] = [
            {'activity_id': key[0], 'activity_name': key[1], 'seconds': value['seconds'],
             'session_count': len(value['session_ids'])}
            for key, value in sorted(distribution.items(), key=lambda item: (-item[1]['seconds'], item[0][1]))
        ]
        response.data['pending_reconciliation_count'] = 0
        response.data['coverage'] = 'complete' if not GoalCompletion.objects.filter(
            scope_key=build_scope_key(request), started_at__isnull=True).exists() else 'partial'
        return response

    @action(detail=False, methods=['get'])
    def history(self, request):
        qs = History.objects.select_related('activity__category__group', 'schedule__premium_period').filter(
            schedule__scope_key=build_scope_key(request), end_time__isnull=False).order_by('-start_time')
        if request.query_params.get('activity_id'):
            qs = qs.filter(activity_id=request.query_params['activity_id'])
        if request.query_params.get('period_id'):
            qs = qs.filter(schedule__premium_period_id=request.query_params['period_id'])
        if request.query_params.get('origin'):
            qs = qs.filter(schedule__execution_origin=request.query_params['origin'])
        try:
            limit = min(max(int(request.query_params.get('limit', 20)), 1), 100)
            offset = max(int(request.query_params.get('offset', 0)), 0)
        except ValueError:
            return Response({'code': 'invalid_pagination'}, status=400)
        page = list(qs[offset:offset + limit])
        return Response({'count': qs.count(), 'results': [{**HistorySerializer(x).data,
            'execution_id': x.schedule_id, 'origin': x.schedule.execution_origin,
            'duration_seconds': int((x.end_time-x.start_time).total_seconds()),
            'premium_period_id': x.schedule.premium_period_id} for x in page]})


class GameplayTrackingSettingsViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]

    def list(self, request):
        from .services.premium_reporting import settings_for
        return Response(settings_for(build_scope_key(request)))

    def partial_update(self, request, pk=None):
        scope = build_scope_key(request)
        with transaction.atomic():
            obj = GameplayTrackingSettings.objects.select_for_update().filter(scope_key=scope).first()
            expected_version = request.data.get('expected_version')
            if expected_version is None:
                return Response({'code': 'expected_version_required'}, status=400)
            current_version = obj.version if obj else 0
            if int(expected_version) != current_version:
                return Response({'code': 'stale_settings_version'}, status=409)
            if obj is None:
                obj = GameplayTrackingSettings(scope_key=scope)
            minutes = int(request.data.get('daily_reference_minutes', obj.daily_reference_minutes))
            if not 1 <= minutes <= 1440:
                return Response({'code': 'invalid_daily_reference'}, status=400)
            groups = Group.objects.filter(id__in=request.data.get('group_ids', []))
            if groups.count() != len(set(request.data.get('group_ids', []))):
                return Response({'code': 'group_not_found'}, status=400)
            obj.daily_reference_minutes, obj.version = minutes, current_version + 1
            if obj.pk:
                obj.save(update_fields=['daily_reference_minutes', 'version', 'updated_at'])
            else:
                obj.save()
            obj.groups.set(groups)
        from .services.premium_reporting import settings_for
        return Response(settings_for(scope))
