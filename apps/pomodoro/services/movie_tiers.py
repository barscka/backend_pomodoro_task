"""Classificação pessoal com limites e rebalanceamento atômico por coleção."""
from collections import Counter

from django.db import transaction
from django.db.models import F

from ..models import MovieCollectionEntry, MovieProgress, MovieTierRating, MovieTierState
from ..repositories import movies
from .movie_draw import MovieError, fingerprint, replay, remember

LIMITS = {'S': 10, 'A': 20, 'B': 20, 'C': 20, 'D': 30}


def board_data(scope, collection_id):
    state = MovieTierState.objects.filter(scope_key=scope, collection_id=collection_id).first()
    watched = MovieProgress.objects.filter(scope_key=scope, status='watched').values('movie_id')
    entries = MovieCollectionEntry.objects.filter(collection_id=collection_id, movie_id__in=watched).select_related('movie__activity')
    ratings = dict(MovieTierRating.objects.filter(scope_key=scope, collection_id=collection_id).values_list('movie_id', 'tier'))
    items = [{'movie_id': entry.movie_id, 'name': entry.movie.activity.name,
              'release_year': entry.movie.release_year, 'tier': ratings.get(entry.movie_id),
              'active': entry.movie.active and entry.movie.activity.active}
             for entry in entries.order_by('movie__activity__name', 'movie_id')]
    counts = Counter(item['tier'] for item in items)
    return {'collection_id': collection_id, 'version': state.version if state else 0,
            'limits': LIMITS, 'counts': {tier: counts[tier] for tier in LIMITS},
            'unrated_count': counts[None], 'watched_count': len(items), 'items': items}


@transaction.atomic
def get_board(scope, collection_id):
    movies.collection(collection_id, active_only=False)
    movies.lock_states(scope)
    return board_data(scope, int(collection_id))


@transaction.atomic
def rebalance(scope, collection_id, data):
    collection = movies.collection(collection_id)
    movies.lock_states(scope)
    digest = fingerprint('movie_tiers', collection.pk, data)
    prior = replay(scope, data, digest)
    if prior is not None:
        return prior
    state, _ = MovieTierState.objects.get_or_create(scope_key=scope, collection=collection)
    if state.version != data['expected_version']:
        raise MovieError('stale_tier_version', 'Sua tier list mudou. Atualize e revise a classificação.')
    board = board_data(scope, collection.pk)
    watched = {item['movie_id'] for item in board['items']}
    membership = set(MovieCollectionEntry.objects.filter(collection=collection).values_list('movie_id', flat=True))
    proposed = {item['movie_id']: item['tier'] for item in board['items']}
    for move in data['moves']:
        if move['movie_id'] not in membership:
            raise MovieError('movie_outside_collection', 'O filme não pertence a esta coleção.')
        if move['tier'] is not None and move['movie_id'] not in watched:
            raise MovieError('movie_not_watched', 'Assista ao filme antes de classificá-lo.')
        proposed[move['movie_id']] = move['tier']
    counts = Counter(proposed.values())
    for tier, limit in LIMITS.items():
        if counts[tier] > limit:
            raise MovieError('tier_full', f'O tier {tier} aceita até {limit} filmes. Mova outro filme para liberar uma vaga.',
                             tier=tier, limit=limit, count=counts[tier])
    # Validar o resultado completo antes de gravar permite trocar tiers cheios.
    for move in data['moves']:
        query = MovieTierRating.objects.filter(scope_key=scope, collection=collection, movie_id=move['movie_id'])
        if move['tier'] is None:
            query.delete()
        else:
            MovieTierRating.objects.update_or_create(scope_key=scope, collection=collection,
                movie_id=move['movie_id'], defaults={'tier': move['tier']})
    state.version += 1
    state.save(update_fields=['version'])
    return remember(scope, data, digest, board_data(scope, collection.pk))


def clear_movie(scope, movie_id):
    """Chamar dentro da transação e do mutex do progresso ao desfazer assistido."""
    rows = MovieTierRating.objects.filter(scope_key=scope, movie_id=movie_id)
    collections = list(rows.values_list('collection_id', flat=True))
    rows.delete()
    MovieTierState.objects.filter(scope_key=scope, collection_id__in=collections).update(version=F('version') + 1)
