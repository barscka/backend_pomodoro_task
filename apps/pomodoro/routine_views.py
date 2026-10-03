from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from .routine_serializers import (
    ActivityPreferenceSerializer, ExceptionSerializer, IdentitySerializer, IntervalSerializer,
    RevisionSerializer, SelectionSerializer, StartSerializer, SuspensionSerializer, TemplateSerializer,
)
from .serializers import ActivityExecutionSerializer
from .services.activity_execution import ActivityExecutionConflict, build_scope_key
from .services.routine_reporting import summary
from .services.routine_selection import preview, start_from_preview
from .services import routines


class RoutineViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAPIKey]
    http_method_names = ['get', 'post', 'patch', 'put', 'head', 'options']

    def handle_exception(self, exc):
        if isinstance(exc, routines.RoutineError):
            return Response({'code': exc.code, 'detail': exc.detail, **exc.payload}, status=exc.status)
        if isinstance(exc, ActivityExecutionConflict):
            return Response({'code': exc.code, 'detail': exc.detail, **exc.payload}, status=409)
        return super().handle_exception(exc)

    def validated(self, cls, *, query=False):
        data = dict(self.request.query_params.items()) if query else self.request.data
        serializer = cls(data=data)
        if not serializer.is_valid():
            raise routines.RoutineError('invalid_interval' if cls == IntervalSerializer else 'invalid_routine',
                                        'Dados inválidos.', fields=serializer.errors)
        return serializer.validated_data

    def scope(self):
        return build_scope_key(self.request)

    def list(self, request):
        return Response(routines.plan_payload(self.scope()))

    def create(self, request):
        return Response(routines.save_revision(self.scope(), self.validated(RevisionSerializer)), status=201)

    def update_plan(self, request):
        return Response(routines.save_revision(self.scope(), self.validated(RevisionSerializer)))

    @action(detail=False, methods=['post'])
    def template(self, request):
        return Response(routines.save_revision(self.scope(), self.validated(TemplateSerializer), template=True))

    @action(detail=False, methods=['put'])
    def exception(self, request):
        return Response(routines.save_exception(self.scope(), self.validated(ExceptionSerializer)))

    @action(detail=False, methods=['put'])
    def suspension(self, request):
        return Response(routines.save_suspension(self.scope(), self.validated(SuspensionSerializer)))

    @action(detail=False, methods=['get'])
    def agenda(self, request):
        return Response(routines.expand(self.scope(), **self.validated(IntervalSerializer, query=True)))

    @action(detail=False, methods=['get'])
    def context(self, request):
        return Response(routines.context(self.scope()))

    @action(detail=False, methods=['get', 'put'], url_path='activity-preference')
    def activity_preference(self, request):
        if request.method == 'PUT':
            return Response(routines.save_profile(self.scope(), self.validated(ActivityPreferenceSerializer)))
        raw = request.query_params.get('activity_id', '')
        if not raw.isdigit() or len(raw) > 18:
            raise routines.RoutineError('invalid_routine', 'Informe activity_id inteiro positivo.')
        return Response(routines.profile_payload(self.scope(), int(raw)))

    @action(detail=False, methods=['get', 'put'])
    def selection(self, request):
        if request.method == 'PUT':
            return Response(routines.save_selection(self.scope(), self.validated(SelectionSerializer)))
        return Response(routines.selection_payload(self.scope(), **self.validated(IdentitySerializer, query=True)))

    @action(detail=False, methods=['get'])
    def preview(self, request):
        result, _ = preview(self.scope(), **self.validated(IdentitySerializer, query=True))
        return Response(result)

    @action(detail=False, methods=['post'])
    def start(self, request):
        schedule, created = start_from_preview(self.scope(), self.validated(StartSerializer))
        return Response(ActivityExecutionSerializer(schedule).data, status=201 if created else 200)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        return Response(summary(self.scope(), **self.validated(IntervalSerializer, query=True)))

    @action(detail=False, methods=['get'])
    def recommendations(self, request):
        raw = request.query_params.get('page', '1')
        if not raw.isdigit() or len(raw) > 6 or int(raw) < 1:
            raise routines.RoutineError('invalid_routine', 'Página deve ser positiva.')
        return Response(routines.recommendations(self.scope(), int(raw)))
