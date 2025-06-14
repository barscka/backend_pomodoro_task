# apps/pomodoro/views.py
from django.db import models
from django.db.models import F
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Activity, Category, History, Schedule
from .serializers import ActivitySerializer

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
        Retorna a próxima atividade recomendada com base nas regras:
        1. Categorias que ainda não atingiram o limite diário
        2. Atividades não executadas recentemente (priorizando as mais antigas)
        """
        try:
            today = timezone.now().date()
            
            # Filtra categorias que ainda podem executar
            available_category_ids = [
                cat.id for cat in Category.objects.all() 
                if cat.can_execute_more()
            ]
            
            if not available_category_ids:
                return Response(
                    {"detail": "Limites diários atingidos para todas as categorias"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Pega atividades dessas categorias, ordenando por:
            # 1. Atividades nunca executadas (last_executed=None) primeiro
            # 2. Depois pelas mais antigas
            activity = (
                Activity.objects
                .filter(category_id__in=available_category_ids)
                .order_by(
                    models.Case(
                        models.When(last_executed__isnull=True, then=models.Value(0)),
                        models.When(last_executed__isnull=False, then=models.Value(1)),
                        output_field=models.IntegerField()
                    ),
                    'last_executed'  # Ordena as executadas pelas mais antigas
                )
                .first()
            )
            
            if not activity:
                return Response(
                    {"detail": "Nenhuma atividade disponível nas categorias permitidas"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            return Response(self.get_serializer(activity).data)
        
        
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """
        Endpoint: POST /api/activities/<id>/start/
        Registra o início de uma atividade Pomodoro
        """
        try:
            activity = self.get_object()
            
            # Verifica se a categoria ainda permite execuções
            if not activity.category.can_execute_more():
                return Response(
                    {"error": f"Limite diário de {activity.category.max_daily_executions} execuções atingido para esta categoria"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Cria o registro no histórico
            History.objects.create(
                activity=activity,
                start_time=timezone.now()
            )
            
            # Atualiza o último horário de execução
            activity.last_executed = timezone.now()
            activity.save()
            
            return Response(
                {
                    "status": "Atividade iniciada com sucesso",
                    "activity": self.get_serializer(activity).data
                },
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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