# apps/pomodoro/views.py
from django.db import models
from django.db.models import F
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Activity, Category, History, Schedule
from .serializers import ActivitySerializer, HistorySerializer

class ActivityViewSet(viewsets.ModelViewSet):
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
        Lógica:
        1. Ignora atividades já concluídas HOJE (history com end_time hoje)
        2. Ignora categorias que já atingiram 2 execuções HOJE
        3. Seleciona aleatoriamente entre as atividades restantes
        """
        try:
            today = timezone.now().date()

            # 1. Busca IDs de atividades já concluídas hoje
            completed_today = History.objects.filter(
                end_time__date=today
            ).values_list('activity_id', flat=True)

            # 2. Busca categorias que já atingiram o limite (2 execuções hoje)
            from django.db.models import Count
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
                .exclude(id__in=completed_today)  # Ignora concluídas hoje
                .exclude(category_id__in=exhausted_categories)  # Ignora categorias esgotadas
                .order_by('?')  # Ordem aleatória
            )

            activity = eligible_activities.first()

            if not activity:
                return Response(
                    {
                        "detail": "Nenhuma atividade disponível",
                        "reason": "Todas as atividades foram concluídas hoje ou categorias atingiram o limite"
                    },
                    status=status.HTTP_404_NOT_FOUND
                )

            return Response(self.get_serializer(activity).data)

        except Exception as e:
            return Response(
                {"error": f"Erro ao buscar próxima atividade: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """
        POST /api/activities/<id>/start/
        Inicia uma atividade e retorna:
        - ID da atividade
        - Nome da tarefa
        - Categoria
        - Data e hora do agendamento criado
        """
        try:
            activity = self.get_object()
            now = timezone.now()
            
            # Verifica limite da categoria
            if not activity.category.can_execute_more():
                return Response(
                    {"error": f"Limite diário de {activity.category.max_daily_executions} execuções atingido"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 1. Cria registro no histórico
            History.objects.create(
                activity=activity,
                start_time=now
            )
            
            # 2. Cria/atualiza agendamento
            schedule, created = Schedule.objects.update_or_create(
                activity=activity,
                scheduled_date=now.date(),
                defaults={
                    'start_time': now.time(),
                    'completed': False
                }
            )
            
            # 3. Atualiza atividade
            activity.last_executed = now
            activity.save()
            
            # Resposta formatada
            response_data = {
                "id": activity.id,
                "name": activity.name,
                "category": {
                    "id": activity.category.id,
                    "name": activity.category.name
                },
                "schedule": {
                    "id": schedule.id,
                    "date": schedule.scheduled_date,
                    "start_time": schedule.start_time.strftime("%H:%M:%S") if schedule.start_time else None
                }
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """
        Endpoint: POST /api/activities/<id>/complete/
        Finaliza uma atividade, registrando no histórico e agendamento
        """
        try:
            activity = self.get_object()
            now = timezone.now()
            
            # 1. Atualiza o histórico (encerra a execução atual)
            history_entry = History.objects.filter(
                activity=activity,
                end_time__isnull=True
            ).order_by('-start_time').first()
            
            if history_entry:
                history_entry.end_time = now
                history_entry.duration = (now - history_entry.start_time).seconds // 60
                history_entry.save()
            
            # 2. Cria/atualiza registro no Schedule
            schedule_entry, created = Schedule.objects.get_or_create(
                activity=activity,
                scheduled_date=now.date(),
                defaults={'completed': True}
            )
            
            if not created:
                schedule_entry.completed = True
                schedule_entry.save()
            
            # 3. Atualiza a atividade
            activity.last_executed = now
            activity.save()
            
            return Response(
                {
                    "status": "Atividade finalizada com sucesso",
                    "history_id": history_entry.id if history_entry else None,
                    "schedule_id": schedule_entry.id
                },
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
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