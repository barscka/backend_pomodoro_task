"""Consultas e bloqueios do domínio Filmes."""
from django.db.models import OuterRef, Subquery, Value, CharField, F
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404

from apps.pomodoro.models import (Movie, MovieCollection, MovieCollectionEntry,
                                 MovieDrawState, MovieProgress, MovieProgressEvent, MovieScopeLock)


def collection(pk, *, active_only=True):
    query = MovieCollection.objects.all()
    if active_only:
        query = query.filter(active=True)
    return get_object_or_404(query, pk=pk)


def movie(pk, *, lock=False):
    query = Movie.objects.select_related('activity')
    return get_object_or_404(query.select_for_update() if lock else query, pk=pk)


def progress(scope, movie_id, *, lock=False):
    query = MovieProgress.objects.filter(scope_key=scope, movie_id=movie_id)
    return (query.select_for_update() if lock else query).first()


def entries(collection_id):
    return MovieCollectionEntry.objects.select_related('movie__activity', 'collection').filter(
        collection_id=collection_id, collection__active=True, movie__active=True,
        movie__activity__active=True,
        movie__activity__category_id=F('collection__category_id'),
    ).order_by('award_year', 'movie__release_year', 'movie__activity__name', 'movie_id')


def catalog(scope, params):
    query = Movie.objects.select_related('activity').filter(active=True, activity__active=True)
    if params.get('collection_id'):
        query = query.filter(id__in=entries(params['collection_id']).values('movie_id'))
        query = query.annotate(award_year=Subquery(entries(params['collection_id']).filter(movie_id=OuterRef('pk')).values('award_year')[:1]))
    else:
        query = query.annotate(award_year=Value(None, output_field=CharField()))
    query = query.annotate(movie_status=Coalesce(Subquery(MovieProgress.objects.filter(
        scope_key=scope, movie_id=OuterRef('pk')).values('status')[:1]), Value('unwatched')))
    if params.get('status'):
        query = query.filter(movie_status=params['status'])
    if params.get('search'):
        query = query.filter(activity__name__icontains=params['search'])
    return query.order_by('award_year', 'release_year', 'activity__name', 'id')


def lock_states(scope):
    # Ordem global determinística por escopo: também protege criação de progresso
    # ausente e replay do mesmo UUID em coleções diferentes.
    MovieScopeLock.objects.get_or_create(scope_key=scope)
    MovieScopeLock.objects.select_for_update().get(scope_key=scope)
    result = {}
    for pk in MovieCollection.objects.order_by('pk').values_list('pk', flat=True):
        MovieDrawState.objects.get_or_create(scope_key=scope, collection_id=pk)
        result[pk] = MovieDrawState.objects.select_for_update().get(scope_key=scope, collection_id=pk)
    return result


def history(scope, params):
    query = MovieProgressEvent.objects.select_related('movie__activity').filter(scope_key=scope)
    if params.get('collection_id'):
        query = query.filter(movie__entries__collection_id=params['collection_id'])
    return query.order_by('-occurred_at', '-id')


def collections():
    return MovieCollection.objects.filter(active=True).order_by('name', 'id')


def draw(**filters):
    from apps.pomodoro.models import MovieDraw
    return get_object_or_404(MovieDraw.objects.select_related('movie__activity', 'collection'), **filters)


def create_draw(**values):
    from apps.pomodoro.models import MovieDraw
    return MovieDraw.objects.create(**values)


def mutation(scope, request_id):
    from apps.pomodoro.models import MovieMutation
    return MovieMutation.objects.filter(scope_key=scope, request_id=request_id).first()


def remember(**values):
    from apps.pomodoro.models import MovieMutation
    return MovieMutation.objects.create(**values)


def progresses(scope, movie_ids):
    return MovieProgress.objects.filter(scope_key=scope, movie_id__in=movie_ids)


def record_event(**values):
    return MovieProgressEvent.objects.create(**values)


def current_state(collection, scope):
    return MovieDrawState.objects.select_related('current_draw__movie__activity').filter(
        collection=collection, scope_key=scope).first()


def memberships(movie):
    return movie.entries.order_by('collection_id').values('collection_id', 'award_year', 'award_edition')


def save(instance, **kwargs):
    instance.save(**kwargs)
