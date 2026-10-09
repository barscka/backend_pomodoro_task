"""Locks reais: executar somente no PostgreSQL efêmero do script dedicado."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature

from .models import MovieDraw, MovieProgress, MovieProgressEvent, MovieMutation, MovieCollection, MovieCollectionEntry
from .services import movie_draw
from .test_spec_018 import fixture


@skipUnlessDBFeature('has_select_for_update')
class MoviePostgresTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql')
        self.group, self.category, self.collection, self.movie = fixture()
        self.scope = 'movies-concurrency-test'

    def parallel(self, functions):
        barrier = Barrier(len(functions))

        def run(func):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ('ok', func())
            except movie_draw.MovieError as exc:
                return ('conflict', exc.code)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return list(pool.map(run, functions))

    def test_first_concurrent_draws_have_one_pending(self):
        def draw():
            response, created = movie_draw.draw_movie(self.scope, self.collection.pk,
                {'request_id': uuid4(), 'expected_state_version': 0})
            return response['id']
        results = self.parallel([draw, draw])
        self.assertEqual(sum(row[0] == 'ok' for row in results), 1, results)
        self.assertIn(('conflict', 'stale_state_version'), results)
        self.assertEqual(MovieDraw.objects.filter(status='pending').count(), 1)
        self.assertEqual(MovieMutation.objects.count(), 1)

    def test_first_concurrent_same_request_replays_identically(self):
        body = {'request_id': uuid4(), 'expected_state_version': 0}
        def draw():
            return movie_draw.draw_movie(self.scope, self.collection.pk, body)[0]
        results = self.parallel([draw, draw])
        self.assertEqual(results[0][0], 'ok', results)
        self.assertEqual(results[0], results[1])
        self.assertEqual(MovieDraw.objects.count(), 1)
        self.assertEqual(MovieMutation.objects.count(), 1)

    def test_initial_progress_creation_has_one_version_winner(self):
        def change(status):
            return lambda: movie_draw.mutate(self.scope, 'progress', self.movie.pk,
                {'request_id': uuid4(), 'expected_version': 0, 'status': status})
        results = self.parallel([change('watching'), change('watched')])
        self.assertEqual(sum(row[0] == 'ok' for row in results), 1, results)
        self.assertIn(('conflict', 'stale_progress_version'), results)
        self.assertEqual(MovieProgress.objects.get().version, 1)
        self.assertEqual(MovieProgressEvent.objects.count(), 1)

    def test_existing_progress_conflict_and_idempotent_event(self):
        movie_draw.mutate(self.scope, 'progress', self.movie.pk,
            {'request_id': uuid4(), 'expected_version': 0, 'status': 'watching'})
        body = {'request_id': uuid4(), 'expected_version': 1, 'status': 'watched'}
        def complete():
            return movie_draw.mutate(self.scope, 'progress', self.movie.pk, body)
        results = self.parallel([complete, complete])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 'ok', results)
        self.assertEqual(MovieProgress.objects.get().version, 2)
        self.assertEqual(MovieProgressEvent.objects.count(), 2)

    def test_accept_competing_completion_never_reopens_watched(self):
        draw, _ = movie_draw.draw_movie(self.scope, self.collection.pk,
            {'request_id': uuid4(), 'expected_state_version': 0})
        def accept():
            return movie_draw.mutate(self.scope, 'accept', draw['id'],
                {'request_id': uuid4(), 'expected_state_version': 1, 'expected_progress_version': 0}, self.collection.pk)
        def complete():
            return movie_draw.mutate(self.scope, 'progress', self.movie.pk,
                {'request_id': uuid4(), 'expected_version': 0, 'status': 'watched'})
        results = self.parallel([accept, complete])
        self.assertEqual(sum(row[0] == 'ok' for row in results), 1, results)
        self.assertIn(MovieProgress.objects.get().status, ('watching', 'watched'))
        self.assertEqual(MovieProgressEvent.objects.count(), 1)
        self.assertEqual(MovieDraw.objects.filter(status='pending').count(), 0)

    def test_same_request_different_collections_is_conflict(self):
        other = MovieCollection.objects.create(slug='second', name='Segunda', category=self.category)
        MovieCollectionEntry.objects.create(collection=other, movie=self.movie, award_year=2000)
        body = {'request_id': uuid4(), 'expected_state_version': 0}
        results = self.parallel([
            lambda: movie_draw.draw_movie(self.scope, self.collection.pk, body),
            lambda: movie_draw.draw_movie(self.scope, other.pk, body),
        ])
        self.assertEqual(sum(row[0] == 'ok' for row in results), 1, results)
        self.assertIn(('conflict', 'idempotency_conflict'), results)
        self.assertEqual(MovieDraw.objects.count(), 1)
