from apps.pomodoro.repositories import movies


def progress_data(scope, movie_id):
    row = movies.progress(scope, movie_id)
    return {'status': row.status if row else 'unwatched', 'version': row.version if row else 0,
            'started_at': row.started_at.isoformat() if row and row.started_at else None,
            'watched_at': row.watched_at.isoformat() if row and row.watched_at else None}


def movie_data(movie, scope, award_year=None):
    return {'id': movie.pk, 'activity_id': movie.activity_id, 'name': movie.activity.name,
            'description': movie.activity.description, 'release_year': movie.release_year,
            'award_year': award_year, 'runtime_minutes': movie.runtime_minutes,
            'poster_url': movie.poster_url, 'watch_url': movie.watch_url,
            'active': movie.active and movie.activity.active,
            'progress': progress_data(scope, movie.pk)}


def draw_valid(draw, scope):
    return (movies.entries(draw.collection_id).filter(movie_id=draw.movie_id).exists()
            and progress_data(scope, draw.movie_id)['status'] == 'unwatched')


def draw_data(draw, scope):
    return {'id': str(draw.pk), 'request_id': str(draw.request_id), 'status': draw.status,
            'animation_duration_ms': draw.animation_duration_ms, 'eligible_count': draw.eligible_count,
            'selected_index': draw.selected_index, 'candidates': draw.candidate_snapshot,
            'selected_at': draw.selected_at.isoformat(),
            'resolved_at': draw.resolved_at.isoformat() if draw.resolved_at else None,
            'movie': movie_data(draw.movie, scope, draw.candidate_snapshot[draw.selected_index]['award_year'])}


def state_data(collection, scope, state=None):
    if state is None:
        state = movies.current_state(collection, scope)
    entries = list(movies.entries(collection.pk))
    counts = {'total_active': len(entries), 'unwatched': 0, 'watching': 0, 'watched': 0}
    statuses = {p.movie_id: p.status for p in collection_progress(scope, entries)}
    for entry in entries:
        counts[statuses.get(entry.movie_id, 'unwatched')] += 1
    draw = state.current_draw if state and state.current_draw_id else None
    valid = bool(draw and draw.status == 'pending' and draw_valid(draw, scope))
    counts['eligible_count'] = counts['unwatched'] - int(valid)
    condition = ('empty_collection' if not entries else 'collection_completed'
                 if counts['watched'] == len(entries) else 'no_eligible_movies'
                 if not counts['eligible_count'] and not valid else 'ready')
    return {'collection_id': collection.pk, 'version': state.version if state else 0,
            'counts': counts, 'condition': condition,
            'current_draw': draw_data(draw, scope) if draw else None,
            'current_draw_valid': valid}


def collection_progress(scope, entries):
    return movies.progresses(scope, [e.movie_id for e in entries])


def collection_data(collection, scope):
    return {'id': collection.pk, 'slug': collection.slug, 'name': collection.name,
            'category_id': collection.category_id, **state_data(collection, scope)}
