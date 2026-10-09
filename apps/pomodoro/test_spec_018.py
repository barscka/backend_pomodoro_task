import hashlib
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import (Activity, ActivityQueue, ActivityQueueItem, Category, Group, History,
                     Schedule, GoalCompletion, Movie, MovieCollection, MovieCollectionEntry,
                     MovieDraw, MovieDrawState, MovieProgress, MovieProgressEvent, MovieMutation)
from .services import movie_draw, movie_catalog
from .services.activity_queue import eligible_activities, get_or_create_active_queue, recreate_active_queue, close_queue, finalize_queue_if_finished
from .services.activity_queue_reconciliation import reconcile_activity, reconcile_premium_queue


def fixture():
    group = Group.objects.create(name='Entretenimento sintético')
    category = Category.objects.create(name='Oscar sintético', group=group, max_daily_executions=50)
    collection = MovieCollection.objects.create(slug='oscar-best-picture', name='Oscar sintético', category=category)
    activity = Activity.objects.create(name='Filme sintético', category=category, duration=25)
    movie = Movie.objects.create(activity=activity, release_year=1990, runtime_minutes=120)
    MovieCollectionEntry.objects.create(collection=collection, movie=movie, award_year=1991)
    return group, category, collection, movie


class MovieApiTests(APITestCase):
    def setUp(self):
        _, key = APIKey.objects.create_key(name='movies')
        self.auth = f'Api-Key {key}'
        self.scope = hashlib.sha256(self.auth.encode()).hexdigest()
        self.client.credentials(HTTP_AUTHORIZATION=self.auth)
        self.group, self.category, self.collection, self.movie = fixture()
        self.base = f'/api/movie-collections/{self.collection.pk}'

    def state(self):
        response = self.client.get(self.base + '/state/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def draw(self, body=None):
        body = body or {'request_id': str(uuid4()), 'expected_state_version': self.state()['version']}
        return self.client.post(self.base + '/draws/', body, format='json')

    def progress(self, status, version=0, **extra):
        body = {'request_id': str(uuid4()), 'expected_version': version, 'status': status, **extra}
        return self.client.patch(f'/api/movies/{self.movie.pk}/progress/', body, format='json')

    def test_full_flow_replay_timeout_accept_watched_undo(self):
        body = {'request_id': str(uuid4()), 'expected_state_version': 0}
        first = self.draw(body)
        self.assertEqual(first.status_code, 201, first.data)
        replay = self.draw(body)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.data, replay.data)
        recovered = self.client.get(self.base + f'/draws/by-request/{body["request_id"]}/')
        self.assertEqual(recovered.data['id'], first.data['id'])
        self.assertEqual(recovered.data['candidates'], first.data['candidates'])
        accept_body = {'request_id': str(uuid4()), 'expected_state_version': 1, 'expected_progress_version': 0}
        accept_url = self.base + f'/draws/{first.data["id"]}/accept/'
        accepted = self.client.post(accept_url, accept_body, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data['progress']['status'], 'watching')
        self.assertIsNone(accepted.data['progress']['watched_at'])
        self.assertEqual(self.client.post(accept_url, accept_body, format='json').data, accepted.data)
        self.assertEqual(self.draw().data['code'], 'no_eligible_movies')
        watched = self.progress('watched', 1)
        self.assertEqual(watched.status_code, 200, watched.data)
        self.assertEqual(self.state()['condition'], 'collection_completed')
        self.assertEqual(self.draw().data['code'], 'collection_completed')
        self.assertEqual(self.progress('unwatched', 2).status_code, 200)
        current = MovieProgress.objects.get(scope_key=self.scope, movie=self.movie)
        self.assertIsNone(current.started_at)
        self.assertIsNone(current.watched_at)
        self.assertEqual(self.draw().status_code, 201)
        self.assertEqual(MovieProgressEvent.objects.count(), 3)
        self.assertEqual(Schedule.objects.count(), 0)
        self.assertEqual(History.objects.count(), 0)
        self.assertEqual(GoalCompletion.objects.count(), 0)
        self.movie.activity.refresh_from_db()
        self.assertEqual(self.movie.activity.executions_today, 0)
        self.assertIsNone(self.movie.activity.last_executed)
        self.assertEqual(self.movie.activity.duration, 25)

    def test_dismiss_idempotency_and_pending_conflict(self):
        first = self.draw()
        self.assertEqual(self.draw().data['code'], 'pending_draw_exists')
        body = {'request_id': str(uuid4()), 'expected_state_version': 1}
        url = self.base + f'/draws/{first.data["id"]}/dismiss/'
        response = self.client.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(url, body, format='json').data, response.data)
        self.assertEqual(MovieProgressEvent.objects.count(), 0)
        self.assertEqual(self.state()['counts']['eligible_count'], 1)
        self.assertEqual(self.draw().status_code, 201)

    def test_payload_conflict_versions_and_atomicity(self):
        body = {'request_id': str(uuid4()), 'expected_state_version': 0}
        self.draw(body)
        self.assertEqual(self.draw({**body, 'expected_state_version': 1}).data['code'], 'idempotency_conflict')
        response = self.draw({'request_id': str(uuid4()), 'expected_state_version': 0})
        self.assertEqual(response.data['code'], 'stale_state_version')
        self.assertEqual(self.progress('watched', 9).data['code'], 'stale_progress_version')
        self.assertEqual(MovieProgress.objects.count(), 0)
        self.assertEqual(MovieProgressEvent.objects.count(), 0)
        body2 = {'request_id': body['request_id'], 'expected_version': 0, 'status': 'watched'}
        response = self.client.patch(f'/api/movies/{self.movie.pk}/progress/', body2, format='json')
        self.assertEqual(response.data['code'], 'idempotency_conflict')

    def test_progress_replay_manual_completion_resolves_pending(self):
        draw = self.draw()
        body = {'request_id': str(uuid4()), 'expected_version': 0, 'status': 'watched', 'draw_id': draw.data['id']}
        url = f'/api/movies/{self.movie.pk}/progress/'
        response = self.client.patch(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(self.state()['current_draw'])
        self.assertEqual(MovieDraw.objects.get().status, 'accepted')
        self.assertEqual(self.client.patch(url, body, format='json').data, response.data)
        self.assertEqual(MovieProgressEvent.objects.count(), 1)
        conflict = self.client.patch(url, {**body, 'status': 'unwatched'}, format='json')
        self.assertEqual(conflict.data['code'], 'idempotency_conflict')

    def test_scope_isolation_authorization_and_history(self):
        draw = self.draw()
        self.progress('watched')
        history = self.client.get('/api/movies/progress-history/', {'collection_id': self.collection.pk})
        self.assertEqual(history.data['count'], 1)
        _, key = APIKey.objects.create_key(name='other')
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {key}')
        self.assertEqual(self.state()['counts']['watched'], 0)
        self.assertEqual(self.client.get('/api/movies/progress-history/').data['count'], 0)
        missing = self.client.get(self.base + f'/draws/by-request/{draw.data["request_id"]}/')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.draw().status_code, 201)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/movies/').status_code, 403)

    def test_invalidated_choice_read_does_not_write_and_dismiss_releases(self):
        draw = self.draw()
        Movie.objects.filter(pk=self.movie.pk).update(active=False)
        state = self.state()
        self.assertFalse(state['current_draw_valid'])
        self.assertEqual(state['version'], 1)
        self.assertEqual(MovieDraw.objects.get().status, 'pending')
        response = self.client.post(self.base + f'/draws/{draw.data["id"]}/accept/', {
            'request_id': str(uuid4()), 'expected_state_version': 1, 'expected_progress_version': 0}, format='json')
        self.assertEqual(response.data['code'], 'movie_unavailable')
        response = self.client.post(self.base + f'/draws/{draw.data["id"]}/dismiss/', {
            'request_id': str(uuid4()), 'expected_state_version': 1}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MovieDraw.objects.get().status, 'invalidated')
        self.assertEqual(self.draw().data['code'], 'empty_collection')

    def test_uniform_population_beyond_pagination_and_order(self):
        for index in range(35):
            activity = Activity.objects.create(name=f'Filme {index:02}', category=self.category, priority=index + 1)
            movie = Movie.objects.create(activity=activity, release_year=2000)
            MovieCollectionEntry.objects.create(collection=self.collection, movie=movie, award_year=2001)
        listing = self.client.get('/api/movies/', {'collection_id': self.collection.pk, 'page_size': 2})
        self.assertEqual(listing.data['count'], 36)
        self.assertEqual(len(listing.data['results']), 2)
        with patch('apps.pomodoro.services.movie_draw.secrets.choice', side_effect=lambda rows: rows[-1]) as choose:
            with patch('apps.pomodoro.services.movie_draw.secrets.randbelow', return_value=5000):
                response = self.draw()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(choose.call_args.args[0]), 36)
        candidates = response.data['candidates']
        self.assertEqual(len({c['movie_id'] for c in candidates}), 36)
        self.assertEqual(response.data['selected_index'], 35)
        self.assertEqual(response.data['movie']['id'], candidates[-1]['movie_id'])
        self.assertEqual(response.data['animation_duration_ms'], 10000)
        self.assertEqual(self.state()['counts']['eligible_count'], 35)
        self.assertEqual(self.state()['counts']['unwatched'], 36)

    def test_filters_detail_validation_and_collections(self):
        self.progress('watching')
        response = self.client.get('/api/movies/', {'status': 'watching', 'collection_id': self.collection.pk, 'search': 'sintético'})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['award_year'], 1991)
        self.assertEqual(self.client.get('/api/movies/', {'status': 'unwatched'}).data['count'], 0)
        detail = self.client.get(f'/api/movies/{self.movie.pk}/')
        self.assertEqual(detail.data['collections'][0]['award_year'], 1991)
        self.assertEqual(self.client.get('/api/movie-collections/').data['results'][0]['counts']['watching'], 1)
        for params in ({'status': 'invalid'}, {'collection_id': 'x'}, {'page_size': 101}):
            response = self.client.get('/api/movies/', params)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data['code'], 'invalid_input')
        self.assertEqual(self.draw({'request_id': 'bad', 'expected_state_version': 0}).status_code, 400)
        self.assertEqual(self.draw({'request_id': str(uuid4()), 'expected_state_version': 0, 'scope_key': 'other'}).status_code, 400)
        self.assertEqual(self.client.get(self.base + f'/draws/by-request/{uuid4()}/').status_code, 404)

    def test_shared_movie_across_collections_resolves_all_reservations(self):
        second = MovieCollection.objects.create(slug='second', name='Outra', category=self.category)
        MovieCollectionEntry.objects.create(collection=second, movie=self.movie, award_year=2000)
        self.draw()
        movie_draw.draw_movie(self.scope, second.pk, {'request_id': uuid4(), 'expected_state_version': 0})
        self.assertEqual(self.progress('watched').status_code, 200)
        self.assertEqual(MovieDraw.objects.filter(status='pending').count(), 0)
        self.assertEqual(MovieDrawState.objects.filter(current_draw__isnull=False).count(), 0)

    def test_timeout_recovery_and_dismiss_survive_collection_deactivation(self):
        body = {'request_id': str(uuid4()), 'expected_state_version': 0}
        draw = self.draw(body)
        MovieCollection.objects.filter(pk=self.collection.pk).update(active=False)
        self.assertEqual(self.draw(body).data, draw.data)
        recovered = self.client.get(self.base + f'/draws/by-request/{body["request_id"]}/')
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data['id'], draw.data['id'])
        self.assertFalse(self.state()['current_draw_valid'])
        result = self.client.post(self.base + f'/draws/{draw.data["id"]}/dismiss/',
            {'request_id': str(uuid4()), 'expected_state_version': 1}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(MovieDraw.objects.get().status, 'invalidated')
        self.assertEqual(self.draw().data['code'], 'movie_unavailable')

    def test_read_only_state_and_filtering_other_scope_progress(self):
        self.assertEqual(self.state()['version'], 0)
        self.assertEqual(MovieDrawState.objects.count(), 0)
        MovieProgress.objects.create(scope_key='other', movie=self.movie, status='watched', watched_at=timezone.now())
        response = self.client.get('/api/movies/', {'status': 'unwatched'})
        self.assertEqual(response.data['count'], 1)
        response = self.client.get('/api/movies/', {'status': 'watched'})
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(self.client.get('/api/activities/').data, [])

    def test_unknown_or_foreign_draw_does_not_change_progress(self):
        draw = self.draw()
        response = self.progress('watched', draw_id=str(uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(MovieProgressEvent.objects.count(), 0)
        _, key = APIKey.objects.create_key(name='foreign')
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {key}')
        response = self.progress('watched', draw_id=draw.data['id'])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(MovieProgress.objects.count(), 0)

    def test_failed_event_rolls_back_progress_and_reservation(self):
        draw = self.draw()
        with patch('apps.pomodoro.repositories.movies.record_event', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                movie_draw.mutate(self.scope, 'progress', self.movie.pk,
                    {'request_id': uuid4(), 'expected_version': 0, 'status': 'watched'})
        self.assertEqual(MovieProgress.objects.count(), 0)
        self.assertEqual(MovieDraw.objects.get().status, 'pending')
        self.assertEqual(self.state()['version'], 1)
        self.assertEqual(MovieMutation.objects.count(), 1)

    def test_generic_category_edit_cannot_break_collection(self):
        other = Category.objects.create(name='Outra categoria', group=self.group)
        response = self.client.patch(f'/api/activities/{self.movie.activity_id}/', {'category': other.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.movie.activity.refresh_from_db()
        self.assertEqual(self.movie.activity.category_id, self.category.pk)


class MovieModelQueueTests(TestCase):
    def setUp(self):
        self.group, self.category, self.collection, self.movie = fixture()

    def test_constraints_metadata_and_read_only_admin(self):
        with self.assertRaises(ValidationError):
            Movie(activity=self.movie.activity, release_year=1800).full_clean()
        self.movie.watch_url = 'ftp://example.com/movie'
        with self.assertRaises(ValidationError):
            self.movie.full_clean()
        other = Category.objects.create(name='Outra', group=self.group)
        self.collection.category = other
        with self.assertRaises(ValidationError):
            self.collection.full_clean()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                MovieProgress.objects.create(scope_key='test', movie=self.movie, status='watching')
        event_admin = admin.site._registry[MovieProgressEvent]
        self.assertFalse(event_admin.has_add_permission(None))
        self.assertFalse(event_admin.has_change_permission(None))
        self.assertFalse(event_admin.has_delete_permission(None))

    def test_queue_creation_all_groups_premium_and_review(self):
        normal = Activity.objects.create(name='Normal', category=self.category)
        Movie.objects.filter(pk=self.movie.pk).update(active=False)
        for group in [self.group, None]:
            self.assertEqual(list(eligible_activities(selected_group=group)), [normal])
            queue = get_or_create_active_queue(scope_key=f'test-{group}', selected_group=group)
            self.assertEqual(list(queue.items.values_list('activity_id', flat=True)), [normal.pk])
        self.assertFalse(self.movie.activity.can_execute())

    def test_conversion_reconciles_pending_and_preserves_open_history(self):
        activity = Activity.objects.create(name='Converter', category=self.category)
        all_group = Group.objects.get(is_default=True)
        queues = []
        for number, mode in enumerate(['normal', 'skipped_review']):
            queue = ActivityQueue.objects.create(scope_key=f'conversion-{number}', group=all_group, mode=mode)
            item = ActivityQueueItem.objects.create(queue=queue, activity=activity, position=1)
            queues.append((queue, item))
        protected = ActivityQueueItem.objects.create(queue=queues[0][0], activity=self.movie.activity, position=2, state='presented')
        schedule = Schedule.objects.create(activity=self.movie.activity, scope_key='open-test', queue_item=protected,
                                           state='running', scheduled_date=timezone.localdate(), start_time=timezone.localtime().time())
        Movie.objects.create(activity=activity, release_year=2000)
        for queue, item in queues:
            item.refresh_from_db()
            self.assertEqual(item.state, 'expired')
        reconcile_activity(self.movie.activity)
        protected.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(protected.state, 'presented')
        self.assertEqual(schedule.state, 'running')

    def test_reconciliation_does_not_insert_premium_movie(self):
        activity = self.movie.activity
        activity.premium = True
        activity.premium_from = timezone.localdate()
        activity.premium_until = timezone.localdate()
        activity.save()
        queue = ActivityQueue.objects.create(scope_key='premium-movie', group=self.group)
        normal = Activity.objects.create(name='Normal', category=self.category)
        ActivityQueueItem.objects.create(queue=queue, activity=normal, position=1)
        with transaction.atomic():
            reconcile_premium_queue(queue)
        self.assertFalse(queue.items.filter(activity=activity).exists())

    def test_recreation_and_skipped_review_cannot_reinsert_movie(self):
        normal = Activity.objects.create(name='Normal recreation', category=self.category)
        queue = ActivityQueue.objects.create(scope_key='recreation', group=self.group)
        ActivityQueueItem.objects.create(queue=queue, activity=self.movie.activity, position=1, state='skipped')
        ActivityQueueItem.objects.create(queue=queue, activity=normal, position=2)
        result = recreate_active_queue(scope_key='recreation', selected_group=self.group, expected_queue_id=queue.pk)
        self.assertEqual(list(result.queue.items.values_list('activity_id', flat=True)), [normal.pk])
        close_queue(result.queue)
        review_source = ActivityQueue.objects.create(scope_key='review', group=self.group)
        ActivityQueueItem.objects.create(queue=review_source, activity=self.movie.activity, position=1, state='skipped')
        ActivityQueueItem.objects.create(queue=review_source, activity=normal, position=2, state='skipped')
        review = finalize_queue_if_finished(review_source)
        self.assertEqual(review.mode, 'skipped_review')
        self.assertEqual(list(review.items.values_list('activity_id', flat=True)), [normal.pk])

    def test_activity_conversion_preserves_completed_history(self):
        activity = Activity.objects.create(name='Com histórico', category=self.category)
        schedule = Schedule.objects.create(activity=activity, scheduled_date=timezone.localdate(),
            start_time=timezone.localtime().time(), state='completed', completed=True)
        history = History.objects.create(activity=activity, schedule=schedule, start_time=timezone.now(), end_time=timezone.now())
        Movie.objects.create(activity=activity, release_year=2000)
        self.assertTrue(History.objects.filter(pk=history.pk).exists())
        schedule.refresh_from_db()
        self.assertEqual(schedule.state, 'completed')

    def test_reconciliation_keeps_review_membership_closed(self):
        queue = ActivityQueue.objects.create(scope_key='locked-review', group=self.group,
            mode='skipped_review', skip_locked=True)
        normal = Activity.objects.create(name='Fora da revisão', category=self.category)
        reconcile_activity(normal)
        self.assertEqual(queue.items.count(), 0)

    def test_all_eligible_indices_can_be_selected(self):
        other = Movie.objects.create(activity=Activity.objects.create(name='Segundo', category=self.category), release_year=2000)
        MovieCollectionEntry.objects.create(collection=self.collection, movie=other, award_year=2001)
        for index in range(2):
            with patch('apps.pomodoro.services.movie_draw.secrets.choice', side_effect=lambda entries, i=index: entries[i]):
                with patch('apps.pomodoro.services.movie_draw.secrets.randbelow', return_value=0):
                    result, created = movie_draw.draw_movie(f'index-{index}', self.collection.pk,
                        {'request_id': uuid4(), 'expected_state_version': 0})
            self.assertTrue(created)
            self.assertEqual(result['selected_index'], index)
            self.assertEqual(result['animation_duration_ms'], 5000)
            self.assertEqual(result['movie']['id'], result['candidates'][index]['movie_id'])
