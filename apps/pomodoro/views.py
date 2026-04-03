# apps/pomodoro/views.py
import json
from datetime import timedelta
from django.db.models import Count, F, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from pprint import pprint, pformat
from rest_framework_api_key.permissions import HasAPIKey
from .models import Activity, Category, Group, History, Schedule
from .serializers import ActivitySerializer, GroupSerializer, HistorySerializer


class GroupViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = GroupSerializer
    queryset = Group.objects.all()


class ActivityViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = ActivitySerializer
    queryset = Activity.objects.all().select_related('category', 'category__group')

    def _get_requested_group(self, request):
        group_id = request.query_params.get('group_id') or request.data.get('group_id')
        if group_id:
            return Group.objects.filter(pk=group_id).first()

        group_name = request.query_params.get('group_name') or request.data.get('group_name')
        if group_name:
            return Group.objects.filter(name__iexact=group_name).first()

        return None
    
    def get_queryset(self):
        queryset = super().get_queryset()
        category_id = self.request.query_params.get('category_id')
        group = self._get_requested_group(self.request)

        if category_id:
            queryset = queryset.filter(category_id=category_id)

        if group and not group.is_default:
            queryset = queryset.filter(category__group=group)

        return queryset
    
    @action(detail=False, methods=['get'])
    def next(self, request):
        """
        GET /api/activities/next/
        Retorna a próxima atividade aleatória seguindo as regras:
        1. Ignora atividades concluídas hoje
        2. O grupo apenas restringe o universo de busca
        3. O limite diario continua sendo sempre por categoria
        4. Ordem aleatória entre as elegíveis
        """
        route = "GET /api/activities/next/"
        try:
            today = timezone.now().date()
            selected_group = self._get_requested_group(request)

            # 1. Atividades concluídas hoje
            completed_today = History.objects.filter(
                end_time__date=today
            ).values_list('activity_id', flat=True)

            eligible_activities = (
                Activity.objects
                .select_related('category', 'category__group')
                .exclude(id__in=completed_today)
            )

            exhausted_categories = []
            if selected_group and not selected_group.is_default:
                eligible_activities = eligible_activities.filter(category__group=selected_group)
 
            exhausted_categories = list(
                Category.objects
                .annotate(
                    executions_today_count=Count(
                        'activities__histories',
                        filter=Q(activities__histories__start_time__date=today),
                    ),
                )
                .filter(executions_today_count__gte=F('max_daily_executions'))
                .values_list('id', flat=True)
            )

            eligible_activities = eligible_activities.exclude(category_id__in=exhausted_categories)

            eligible_activities = eligible_activities.order_by('?')

            activity = eligible_activities.first()

            if not activity:
                response_data = {
                    "detail": "Nenhuma atividade disponível",
                    "reason": "Todas as atividades foram concluídas hoje ou categorias atingiram o limite",
                    "stats": {
                        "completed_today": len(completed_today),
                        "exhausted_categories": len(exhausted_categories),
                    },
                }
                log_response(response_data, status.HTTP_404_NOT_FOUND,route)
                return Response(response_data, status=status.HTTP_404_NOT_FOUND)

            response_data = self.get_serializer(activity).data
            log_response(response_data, status.HTTP_200_OK,route)
            return Response(response_data)

        except Exception as e:
            error_data = {
                "error": "Erro ao buscar próxima atividade",
                "details": str(e),
                "timestamp": timezone.now().isoformat()
            }
            log_response(error_data, status.HTTP_500_INTERNAL_SERVER_ERROR,route)
            return Response(
                error_data,
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """
        POST /api/activities/<id>/start/
        Cria um novo registro de Schedule com data/hora atual
        """
        route = "POST /api/activities/<id>/start/"

        try:
            activity = self.get_object()
            now = timezone.now()
            selected_group = self._get_requested_group(request)

            if not activity.can_execute(selected_group):
                return Response(
                    {"error": "Limite diario da categoria atingido para a atividade selecionada"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            existing_schedule = Schedule.objects.filter(
                activity=activity,
                scheduled_date=now.date(),
                completed=False
            ).first()

            if existing_schedule:
                response_data = {
                    "schedule_id": existing_schedule.id,
                    "status": "not_modified",
                }

                log_response(response_data, status.HTTP_304_NOT_MODIFIED, route)

                return Response(
                    response_data,
                    status=status.HTTP_304_NOT_MODIFIED
                )

            schedule = Schedule.objects.create(
                activity=activity,
                scheduled_date=now.date(),
                start_time=now.time(),
                completed=False
            )

            # 🔹 Start time real direto do timezone.now()
            History.objects.create(
                activity=schedule.activity,
                schedule=schedule,
                start_time=now
            )

            response_data = {
                "schedule_id": schedule.id,
                "activity_id": activity.id,
                "date": schedule.scheduled_date.strftime("%Y-%m-%d"),
                "start_time": schedule.start_time.strftime("%H:%M:%S"),
                "status": "Atividade iniciada"
            }

            log_response(response_data, status.HTTP_201_CREATED, route)
            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response(
                {"error": f"Erro ao iniciar atividade: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


    @action(detail=False, methods=['post'])
    def complete(self, request):
        """
        POST /api/activities/complete/
        Atualiza Schedule e History usando tempo REAL
        """
        route = "POST /api/activities/complete/"

        try:
            schedule_id = request.data.get('schedule_id')
            if not schedule_id:
                return Response(
                    {"error": "O campo schedule_id é obrigatório"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            schedule = Schedule.objects.select_related(
                'activity',
                'execution_history'
            ).get(pk=schedule_id)

            history = schedule.execution_history
            now = timezone.now()

            if schedule.completed:
                return Response(
                    {"status": "Atividade já completada"},
                    status=status.HTTP_200_OK
                )

            expected_end_time = history.start_time + timedelta(
                minutes=schedule.activity.duration
            )
            completion_time = min(now, expected_end_time)
            real_duration_minutes = max(
                int((completion_time - history.start_time).total_seconds() // 60),
                0,
            )

            history.end_time = completion_time
            history.duration = real_duration_minutes
            history.save()

            schedule.end_time = completion_time.time()
            schedule.completed = True
            schedule.save()

            response_data = {
                "status": "Atividade completada com sucesso",
                "history_id": history.id,
                "duration_minutes": real_duration_minutes,
                "completed_at": completion_time.isoformat(),
                "expected_end_time": expected_end_time.isoformat(),
            }

            log_response(response_data, status.HTTP_200_OK, route)
            return Response(response_data, status=status.HTTP_200_OK)

        except Schedule.DoesNotExist:
            return Response(
                {"error": "Agendamento não encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Erro ao completar atividade: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
    @action(detail=False, methods=['get'])
    def history(self, request):
        """
        GET /api/activities/history/
        Retorna o histórico completo de execuções
        Ordenado por data decrescente
        """
        try:
            history_entries = (
                History.objects
                .select_related('activity__category')
                .order_by('-start_time')
            )

            if not history_entries.exists():
                return Response(
                    {
                        "detail": "Nenhum registro de histórico encontrado",
                        "suggestion": "Execute atividades para gerar histórico"
                    },
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = HistorySerializer(history_entries, many=True)
            return Response(serializer.data)

        except Exception as e:
            return Response(
                {
                    "error": "Erro no servidor ao buscar histórico",
                    "details": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



    @action(
        detail=False,
        methods=['get'],
        url_path=r'status/(?P<schedule_id>[^/.]+)'
    )
    def status(self, request, schedule_id=None):
        """
        GET /api/activities/status/<schedule_id>/
        Retorna status temporal REAL
        """
        try:
            schedule = Schedule.objects.select_related(
                'activity',
                'execution_history'
            ).get(pk=schedule_id)

            history = schedule.execution_history
            now = timezone.now()

            duration_minutes = schedule.activity.duration
            total_seconds = duration_minutes * 60

            elapsed_seconds = int(
                (now - history.start_time).total_seconds()
            )

            expected_end_time = history.start_time + timedelta(
                minutes=duration_minutes
            )
            remaining_seconds = max(total_seconds - elapsed_seconds, 0)

            return Response({
                "schedule_id": schedule.id,
                "activity_id": schedule.activity.id,
                "start_time": history.start_time.isoformat(),
                "expected_end_time": expected_end_time.isoformat(),
                "duration_minutes": duration_minutes,
                "elapsed_seconds": elapsed_seconds,
                "remaining_seconds": remaining_seconds,
                "is_completed": schedule.completed
            })

        except Schedule.DoesNotExist:
            return Response(
                {"error": "Schedule não encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Erro ao obter status: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


def log_response(response_data, status_code,route):
    """
    Exibe o response de duas formas:
    1. Formatado bonito no console (para desenvolvimento)
    2. JSON puro para copiar e usar no Postman
    """
    status_color = '32m' if status_code < 400 else '31m'
    console_msg = (
        f"\n\033[1;34m{'='*60}\033[0m\n"
        f"\033[1mAPI Response \033[{status_color}({status_code})\033[0m:\n"
        f"\033[36m{pformat(response_data, indent=2, width=80)}\033[0m\n"
        f"\033[1;34m{'='*60}\033[0m\n"
    )
    print(console_msg)
    print("\n" + "="*60)
    print(f"📤 Rota da API:  {route}")
    print(f"📤 RETORNANDO RESPONSE (status {status_code})")
    print("-"*60)
    
    if isinstance(response_data, dict):
        for key, value in response_data.items():
            if isinstance(value, dict):
                print(f"🔹 {key.upper()}:")
                pprint(value, indent=4, width=80)
            else:
                print(f"🔸 {key}: {value}")
    else:
        pprint(response_data, indent=2, width=80)
    
    print("="*60 + "\n")
    # 2. JSON puro para Postman (em vermelho para diferenciar)
    raw_json = json.dumps(response_data, indent=2, ensure_ascii=False)
    print(f"\033[31m⬇️ JSON PARA POSTMAN ⬇️\n{raw_json}\n\033[0m")
