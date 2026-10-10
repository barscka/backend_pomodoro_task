import hashlib
import json
import secrets

from django.db import transaction
from django.utils import timezone

from apps.pomodoro.models import MovieProgress
from apps.pomodoro.repositories import movies
from .movie_catalog import draw_data, draw_valid, movie_data, progress_data, state_data


class MovieError(Exception):
    def __init__(self, code, detail=None, **extra):
        self.code = code
        self.detail = detail or {
            'stale_state_version': 'O estado da coleção mudou. Atualize antes de tentar novamente.',
            'stale_progress_version': 'O progresso mudou. Atualize antes de tentar novamente.',
            'pending_draw_exists': 'Há uma escolha pendente. Recupere ou dispense essa escolha.',
            'idempotency_conflict': 'O request_id já foi utilizado com outra operação ou payload.',
            'movie_unavailable': 'O filme ou a escolha não está disponível para esta ação.',
            'collection_completed': 'Todos os filmes ativos foram assistidos.',
            'no_eligible_movies': 'Não há filmes elegíveis. Continue assistindo ou devolva um filme à roleta.',
            'empty_collection': 'A coleção não possui filmes ativos cadastrados.',
        }.get(code, code)
        self.extra = extra
        super().__init__(self.detail)


def fail(code, **extra):
    raise MovieError(code, **extra)


def fingerprint(operation, target, data):
    return hashlib.sha256(json.dumps({'operation': operation, 'target': str(target), 'payload': data},
                                    sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def replay(scope, data, digest):
    row = movies.mutation(scope, data['request_id'])
    if row:
        if row.payload_hash != digest:
            fail('idempotency_conflict')
        return row.response


def remember(scope, data, digest, response):
    movies.remember(scope_key=scope, request_id=data['request_id'], payload_hash=digest, response=response)
    return response


def check_state(state, expected):
    if state.version != expected:
        fail('stale_state_version')


def resolve(state, status):
    draw = state.current_draw
    draw.status = status
    draw.resolved_at = timezone.now()
    movies.save(draw, update_fields=['status', 'resolved_at'])
    state.current_draw = None
    state.version += 1
    movies.save(state, update_fields=['current_draw', 'version'])


@transaction.atomic
def draw_movie(scope, collection_id, data):
    collection = movies.collection(collection_id, active_only=False)
    states = movies.lock_states(scope)
    state = states[collection.pk]
    digest = fingerprint('draw', collection.pk, data)
    prior = replay(scope, data, digest)
    if prior is not None:
        return prior, False
    if not collection.active:
        fail('movie_unavailable')
    check_state(state, data['expected_state_version'])
    if state.current_draw_id:
        fail('pending_draw_exists', draw_id=str(state.current_draw_id), valid=draw_valid(state.current_draw, scope))
    entries = list(movies.entries(collection.pk))
    statuses = {p.movie_id: p.status for p in movies.progresses(scope, [e.movie_id for e in entries])}
    eligible = [e for e in entries if statuses.get(e.movie_id, 'unwatched') == 'unwatched']
    if not eligible:
        fail('empty_collection' if not entries else 'collection_completed'
             if all(statuses.get(e.movie_id) == 'watched' for e in entries) else 'no_eligible_movies')
    selected = secrets.choice(eligible)
    selected.movie = movies.movie(selected.movie_id, lock=True)
    if not draw_valid_candidate(selected, scope):
        fail('movie_unavailable')
    snapshot = [{'movie_id': e.movie_id, 'name': e.movie.activity.name,
                 'release_year': e.movie.release_year, 'award_year': e.award_year} for e in eligible]
    draw = movies.create_draw(scope_key=scope, collection=collection, movie=selected.movie,
                                   request_id=data['request_id'], payload_hash=digest,
                                   eligible_count=len(eligible), candidate_snapshot=snapshot,
                                   selected_index=eligible.index(selected), animation_duration_ms=5000 + secrets.randbelow(5001))
    state.current_draw = draw
    state.version += 1
    movies.save(state, update_fields=['current_draw', 'version'])
    response = {**draw_data(draw, scope), 'state_version': state.version, 'state': state_data(collection, scope, state)}
    return remember(scope, data, digest, response), True


def draw_valid_candidate(entry, scope):
    return (entry.movie.active and entry.movie.activity.active
            and movies.entries(entry.collection_id).filter(movie_id=entry.movie_id).exists()
            and progress_data(scope, entry.movie_id)['status'] == 'unwatched')


def recover(scope, collection_id, request_id):
    movies.collection(collection_id, active_only=False)
    draw = movies.draw( scope_key=scope, collection_id=collection_id, request_id=request_id)
    state = state_data(draw.collection, scope)
    return {**draw_data(draw, scope), 'state_version': state['version'], 'state': state}


def update_progress(scope, movie, status, expected, data, digest, draw=None):
    current = movies.progress(scope, movie.pk, lock=True)
    if (current.version if current else 0) != expected:
        fail('stale_progress_version')
    previous = current.status if current else 'unwatched'
    now = timezone.now()
    if current is None:
        current = MovieProgress(scope_key=scope, movie=movie, version=1)
    else:
        current.version += 1
    current.status = status
    if status != 'watched':
        from .movie_tiers import clear_movie
        clear_movie(scope, movie.pk)
    current.started_at = None if status == 'unwatched' else (current.started_at or now)
    current.watched_at = now if status == 'watched' else None
    movies.save(current)
    movies.record_event(scope_key=scope, movie=movie, request_id=data['request_id'],
                                      payload_hash=digest, previous_status=previous, next_status=status, draw=draw)


@transaction.atomic
def mutate(scope, operation, target, data, collection_id=None):
    # Todas as mutações obedecem a mesma ordem; ausência de progresso fica
    # protegida pelo estado da coleção e pela unicidade no banco.
    states = movies.lock_states(scope)
    initial_versions = {pk: state.version for pk, state in states.items()}
    digest = fingerprint(operation, f'{collection_id}:{target}', data)
    prior = replay(scope, data, digest)
    if prior is not None:
        return prior
    draw = None
    if operation in ('accept', 'dismiss'):
        collection = movies.collection(collection_id, active_only=operation != 'dismiss')
        state = states[collection.pk]
        draw = movies.draw(pk=target, scope_key=scope, collection=collection)
        check_state(state, data['expected_state_version'])
        if state.current_draw_id != draw.pk or draw.status != 'pending':
            fail('movie_unavailable')
        movie = movies.movie(draw.movie_id, lock=True)
        if operation == 'dismiss':
            resolve(state, 'dismissed' if draw_valid(draw, scope) else 'invalidated')
        else:
            if not draw_valid(draw, scope):
                fail('movie_unavailable', draw_id=str(draw.pk))
            update_progress(scope, movie, 'watching', data['expected_progress_version'], data, digest, draw)
    else:
        movie = movies.movie(target, lock=True)
        if not movie.active or not movie.activity.active:
            fail('movie_unavailable')
        if data.get('draw_id'):
            draw = movies.draw(pk=data['draw_id'], scope_key=scope, movie=movie)
            state = states[draw.collection_id]
            if state.current_draw_id != draw.pk or not draw_valid(draw, scope):
                fail('movie_unavailable')
        update_progress(scope, movie, data['status'], data['expected_version'], data, digest, draw)
    # watching e watched resolvem reservas em todas as coleções do escopo.
    if operation != 'dismiss' and (operation == 'accept' or data['status'] != 'unwatched'):
        for state in states.values():
            if state.current_draw_id and state.current_draw.movie_id == movie.pk:
                resolve(state, 'accepted')
    # A versão de estado muda também quando progresso muda sem reserva.
    if operation != 'dismiss':
        collection_ids = {row['collection_id'] for row in movies.memberships(movie)}
        for state in states.values():
            if state.collection_id in collection_ids and state.version == initial_versions[state.collection_id]:
                state.version += 1
                movies.save(state, update_fields=['version'])
    collection_ids = {row['collection_id'] for row in movies.memberships(movie)}
    response = {'movie': movie_data(movie, scope), 'progress': progress_data(scope, movie.pk),
                'states': [state_data(s.collection, scope, s) for s in states.values()
                           if s.collection_id in collection_ids]}
    if collection_id:
        response['state'] = state_data(states[int(collection_id)].collection, scope, states[int(collection_id)])
    return remember(scope, data, digest, response)
