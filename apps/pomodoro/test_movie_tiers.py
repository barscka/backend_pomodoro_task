import hashlib
from uuid import uuid4

from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import Activity, Movie, MovieCollectionEntry, MovieProgress, MovieTierRating
from .test_spec_018 import fixture


class MovieTierTests(APITestCase):
    def setUp(self):
        _, key = APIKey.objects.create_key(name='tiers')
        auth = f'Api-Key {key}'
        self.scope = hashlib.sha256(auth.encode()).hexdigest()
        self.client.credentials(HTTP_AUTHORIZATION=auth)
        _, self.category, self.collection, self.movie = fixture()
        self.url = f'/api/movie-collections/{self.collection.pk}/tiers/'

    def watch(self, movie):
        MovieProgress.objects.create(scope_key=self.scope, movie=movie, status='watched', watched_at=timezone.now())

    def change(self, moves, version=None, request_id=None):
        if version is None:
            version = self.client.get(self.url).data['version']
        return self.client.patch(self.url, {'request_id': request_id or str(uuid4()),
            'expected_version': version, 'moves': moves}, format='json')

    def populate(self, size):
        movies = [self.movie]
        for index in range(size - 1):
            activity = Activity.objects.create(name=f'Filme {index}', category=self.category)
            movie = Movie.objects.create(activity=activity, release_year=2000)
            MovieCollectionEntry.objects.create(collection=self.collection, movie=movie, award_year=2001)
            movies.append(movie)
        for movie in movies:
            self.watch(movie)
        return movies

    def test_watched_only_reassessment_clear_and_replay(self):
        response = self.change([{'movie_id': self.movie.pk, 'tier': 'S'}])
        self.assertEqual(response.data['code'], 'movie_not_watched')
        self.watch(self.movie)
        request_id = str(uuid4())
        moves = [{'movie_id': self.movie.pk, 'tier': 'S'}]
        response = self.change(moves, 0, request_id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['counts']['S'], 1)
        self.assertEqual(self.change(moves, 0, request_id).data, response.data)
        self.assertEqual(self.change([{'movie_id': self.movie.pk, 'tier': 'A'}], 0, request_id).data['code'], 'idempotency_conflict')
        self.assertEqual(self.change([{'movie_id': self.movie.pk, 'tier': 'B'}], 0).data['code'], 'stale_tier_version')
        self.assertEqual(self.change([{'movie_id': self.movie.pk, 'tier': 'A'}]).data['counts']['A'], 1)
        self.assertEqual(self.change([{'movie_id': self.movie.pk, 'tier': None}]).data['unrated_count'], 1)
        self.assertEqual(MovieProgress.objects.get().status, 'watched')

    def test_all_capacities_and_full_tier_swap_are_atomic(self):
        movies = self.populate(100)
        tiers = ['S'] * 10 + ['A'] * 20 + ['B'] * 20 + ['C'] * 20 + ['D'] * 30
        response = self.change([{'movie_id': m.pk, 'tier': tier} for m, tier in zip(movies, tiers)])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['counts'], {'S': 10, 'A': 20, 'B': 20, 'C': 20, 'D': 30})
        for tier in 'SABC':
            failed = self.change([{'movie_id': movies[-1].pk, 'tier': tier}])
            self.assertEqual(failed.data['code'], 'tier_full')
        self.assertEqual(self.change([{'movie_id': movies[0].pk, 'tier': 'D'}]).data['code'], 'tier_full')
        swapped = self.change([{'movie_id': movies[-1].pk, 'tier': 'S'}, {'movie_id': movies[0].pk, 'tier': 'D'}])
        self.assertEqual(swapped.status_code, 200, swapped.data)
        self.assertEqual(swapped.data['counts'], response.data['counts'])
        self.assertEqual(MovieTierRating.objects.get(movie=movies[-1]).tier, 'S')
        self.assertEqual(MovieTierRating.objects.get(movie=movies[0]).tier, 'D')

    def test_scope_isolation_and_undo_releases_slot(self):
        self.watch(self.movie)
        self.change([{'movie_id': self.movie.pk, 'tier': 'S'}])
        _, other = APIKey.objects.create_key(name='other-tiers')
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {other}')
        self.assertEqual(self.client.get(self.url).data['items'], [])
        self.assertEqual(self.change([{'movie_id': self.movie.pk, 'tier': 'A'}]).data['code'], 'movie_not_watched')
        from .services.movie_progress import set_progress
        set_progress(self.scope, self.movie.pk, {'status': 'unwatched', 'expected_version': 1, 'request_id': uuid4()})
        self.assertFalse(MovieTierRating.objects.exists())
        from .services.movie_tiers import get_board
        board = get_board(self.scope, self.collection.pk)
        self.assertEqual(board['version'], 2)
        self.assertEqual(board['counts']['S'], 0)

    def test_invalid_batch_and_membership_do_not_write(self):
        self.watch(self.movie)
        for moves in ([], [{'movie_id': self.movie.pk, 'tier': 'X'}],
                      [{'movie_id': self.movie.pk, 'tier': 'S'}] * 2):
            self.assertEqual(self.change(moves).status_code, 400)
        response = self.change([{'movie_id': self.movie.pk, 'tier': 'S'}, {'movie_id': 999999, 'tier': 'A'}])
        self.assertEqual(response.data['code'], 'movie_outside_collection')
        self.assertFalse(MovieTierRating.objects.exists())
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 403)
