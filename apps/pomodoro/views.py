# apps/pomodoro/views.py
from django.db import models
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Activity, Category, History
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
        2. Atividades não executadas recentemente
        """
        try:
            today = timezone.now().date()
            
            # Primeiro encontramos categorias que ainda podem executar
            available_categories = []
            for category in Category.objects.all():
                if category.can_execute_more():
                    available_categories.append(category.id)
            
            # Se não houver categorias disponíveis
            if not available_categories:
                return Response(
                    {"detail": "Limites diários atingidos para todas as categorias"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Pega a atividade mais antiga não executada dessas categorias
            activity = (
                Activity.objects
                .filter(category_id__in=available_categories)
                .order_by('last_executed' if Activity._meta.get_field('last_executed').nulls_last else '-last_executed')
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