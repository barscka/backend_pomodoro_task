# apps/pomodoro/views.py
import json
from django.db import models
from django.db.models import F, Count
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from datetime import datetime, timedelta
from django.utils import timezone
from pprint import pprint,pformat
from rest_framework_api_key.permissions import HasAPIKey
from .models import Activity, Category, History, Schedule
from .serializers import ActivitySerializer, HistorySerializer

class ActivityViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    serializer_class = ActivitySerializer
    queryset = Activity.objects.all().select_related('category')
    
    def get_queryset(self):
        queryset = super().get_queryset()
        category_id = self.request.query_params.get('category_id')
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset
    
    @action(detail=False, methods=['get'])
    def next(self, request):
        """
        GET /api/activities/next/
        Retorna a próxima atividade aleatória seguindo as regras:
        1. Ignora atividades concluídas hoje
        2. Ignora categorias com 2+ execuções hoje
        3. Ordem aleatória entre as elegíveis
        """
        route = "GET /api/activities/next/"
        try:
            today = timezone.now().date()

            # 1. Atividades concluídas hoje
            completed_today = History.objects.filter(
                end_time__date=today
            ).values_list('activity_id', flat=True)

            # 2. Categorias esgotadas (2+ execuções hoje)
            exhausted_categories = (
                History.objects
                .filter(start_time__date=today)
                .values('activity__category')
                .annotate(exec_count=Count('id'))
                .filter(exec_count__gte=2)
                .values_list('activity__category_id', flat=True)
            )

            # 3. Busca atividades elegíveis
            eligible_activities = (
                Activity.objects
                .exclude(id__in=completed_today)
                .exclude(category_id__in=exhausted_categories)
                .order_by('?')  # Random order
            )

            activity = eligible_activities.first()

            if not activity:
                response_data = {
                    "detail": "Nenhuma atividade disponível",
                    "reason": "Todas as atividades foram concluídas hoje ou categorias atingiram o limite",
                    "stats": {
                        "completed_today": len(completed_today),
                        "exhausted_categories": len(exhausted_categories)
                    }
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
            
            # Verifica se já existe um schedule não completado para hoje
            existing_schedule = Schedule.objects.filter(
                activity=activity,
                scheduled_date=now.date(),
                completed=False
            ).first()
            
            if existing_schedule:
                response_data = {
                    "schedule_id": existing_schedule.id,
                    "status": "not_modified",
                    "existing_entry": {
                        "start_time": existing_schedule.start_time.strftime("%H:%M:%S"),
                        "date": existing_schedule.scheduled_date.strftime("%Y-%m-%d"),
                        "activity": existing_schedule.activity.name
                    },
                    "message": "Use o agendamento existente"
                }
                
                # Log para desenvolvimento
                log_response(response_data, status.HTTP_304_NOT_MODIFIED,route)
                
                # Retorna com headers adequados
                return Response(
                    response_data,
                    status=status.HTTP_304_NOT_MODIFIED,
                    headers={
                        'X-Cache-Status': 'exists',
                        'X-Schedule-ID': str(existing_schedule.id)
                    }
                )
            
            # Cria novo schedule
            schedule = Schedule.objects.create(
                activity=activity,
                scheduled_date=now.date(),
                start_time=now.time(),
                completed=False
            )

            # Cria registro no histórico (se tiver model History)
            History.objects.create(
                activity=schedule.activity,
                schedule=schedule,
                start_time=timezone.make_aware(
                    datetime.combine(schedule.scheduled_date, schedule.start_time)
                )
            )
            
            response_data = {
                "schedule_id": schedule.id,
                "activity_id": activity.id,
                "date": schedule.scheduled_date.strftime("%Y-%m-%d"),
                "start_time": schedule.start_time.strftime("%H:%M:%S"),
                "status": "Atividade iniciada"
            }
            log_response(response_data, status.HTTP_201_CREATED,route)
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
        Body: {"schedule_id": X}
        Atualiza Schedule e History usando tempo REAL decorrido
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
                response_data = {
                    "status": "Atividade já completada",
                    "schedule_id": schedule.id,
                    "history_id": history.id,
                    "completed_at": history.end_time.strftime("%Y-%m-%d %H:%M:%S")
                    if history.end_time else None
                }
                log_response(response_data, status.HTTP_200_OK, route)
                return Response(response_data, status=status.HTTP_200_OK)

            # 🔹 Calcula duração REAL
            real_duration_minutes = int(
                (now - history.start_time).total_seconds() // 60
            )

            # Atualiza History
            history.end_time = now
            history.duration = real_duration_minutes
            history.save()

            # Atualiza Schedule
            schedule.end_time = now.time()
            schedule.completed = True
            schedule.save()

            response_data = {
                "status": "Atividade completada com sucesso",
                "schedule": {
                    "id": schedule.id,
                    "completed": True,
                    "start": history.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": now.strftime("%Y-%m-%d %H:%M:%S")
                },
                "history": {
                    "id": history.id,
                    "duration_minutes": real_duration_minutes
                }
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

        
    # apps/pomodoro/views.py
    @action(detail=False, methods=['get'])
    def history(self, request):
        """
        GET /api/activities/history/
        Retorna o histórico completo de execuções com:
        - Detalhes da atividade
        - Detalhes da categoria
        - Status de completude
        Ordenado por data decrescente
        """
        try:
            # Consulta otimizada com select_related
            history_entries = History.objects.select_related(
                'activity__category'
            ).order_by('-start_time')  # Mais recentes primeiro

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
