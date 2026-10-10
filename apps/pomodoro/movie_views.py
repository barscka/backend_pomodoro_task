"""Transporte HTTP fino para catálogo, sorteio e progresso."""
from django.http import Http404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from .movie_serializers import DrawRequest, DismissRequest, AcceptRequest, ProgressRequest, CatalogQuery, TierRequest
from .repositories import movies
from .services import movie_catalog, movie_draw, movie_progress, movie_tiers
from .services.activity_execution import build_scope_key


class MoviePagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class MovieBase(viewsets.ViewSet):
    permission_classes = [HasAPIKey]

    def handle_exception(self, exc):
        if isinstance(exc, movie_draw.MovieError):
            return Response({'code': exc.code, 'detail': exc.detail, **exc.extra}, status=409)
        if isinstance(exc, ValidationError):
            return Response({'code': 'invalid_input', 'detail': exc.detail}, status=400)
        if isinstance(exc, (Http404, NotFound, ValueError)):
            return Response({'code': 'not_found', 'detail': 'Registro não encontrado.'}, status=404)
        return super().handle_exception(exc)

    def body(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def params(self):
        serializer = CatalogQuery(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    @property
    def scope(self):
        return build_scope_key(self.request)

    def paginated(self, query, convert):
        paginator = MoviePagination()
        page = paginator.paginate_queryset(query, self.request, view=self)
        return paginator.get_paginated_response([convert(row) for row in page])


class MovieCollectionViewSet(MovieBase):
    @action(detail=True, methods=['get', 'patch'], url_path='tiers')
    def tiers(self, request, pk=None):
        if request.method == 'GET':
            return Response(movie_tiers.get_board(self.scope, pk))
        return Response(movie_tiers.rebalance(self.scope, pk, self.body(TierRequest)))

    def list(self, request):
        self.params()
        return self.paginated(movies.collections(),
                              lambda row: movie_catalog.collection_data(row, self.scope))

    @action(detail=True, methods=['get'])
    def state(self, request, pk=None):
        return Response(movie_catalog.state_data(movies.collection(pk, active_only=False), self.scope))

    @action(detail=True, methods=['post'])
    def draws(self, request, pk=None):
        result, created = movie_draw.draw_movie(self.scope, pk, self.body(DrawRequest))
        return Response(result, status=201 if created else 200)

    @action(detail=True, methods=['get'], url_path=r'draws/by-request/(?P<request_id>[0-9a-fA-F-]{36})')
    def recover(self, request, pk=None, request_id=None):
        from uuid import UUID
        return Response(movie_draw.recover(self.scope, pk, UUID(request_id)))

    @action(detail=True, methods=['post'], url_path=r'draws/(?P<draw_id>[0-9a-fA-F-]{36})/accept')
    def accept(self, request, pk=None, draw_id=None):
        from uuid import UUID
        return Response(movie_draw.mutate(self.scope, 'accept', UUID(draw_id), self.body(AcceptRequest), pk))

    @action(detail=True, methods=['post'], url_path=r'draws/(?P<draw_id>[0-9a-fA-F-]{36})/dismiss')
    def dismiss(self, request, pk=None, draw_id=None):
        from uuid import UUID
        return Response(movie_draw.mutate(self.scope, 'dismiss', UUID(draw_id), self.body(DismissRequest), pk))


class MovieViewSet(MovieBase):
    def list(self, request):
        params = self.params()
        if params.get('collection_id'):
            movies.collection(params['collection_id'])
        return self.paginated(movies.catalog(self.scope, params),
                              lambda row: movie_catalog.movie_data(row, self.scope, row.award_year))

    def retrieve(self, request, pk=None):
        movie = movies.movie(pk)
        result = movie_catalog.movie_data(movie, self.scope)
        result['collections'] = list(movies.memberships(movie))
        return Response(result)

    @action(detail=True, methods=['patch'])
    def progress(self, request, pk=None):
        return Response(movie_progress.set_progress(self.scope, pk, self.body(ProgressRequest)))

    @action(detail=False, methods=['get'], url_path='progress-history')
    def progress_history(self, request):
        params = self.params()
        if params.get('collection_id'):
            movies.collection(params['collection_id'])
        return self.paginated(movies.history(self.scope, params), lambda row: {
            'id': row.pk, 'movie_id': row.movie_id, 'name': row.movie.activity.name,
            'request_id': str(row.request_id), 'previous_status': row.previous_status,
            'next_status': row.next_status, 'occurred_at': row.occurred_at.isoformat(),
            'draw_id': str(row.draw_id) if row.draw_id else None})
