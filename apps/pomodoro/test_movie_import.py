from pathlib import Path

import tablib
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Activity, Category, Group, Movie, MovieCollection, MovieCollectionEntry, MovieProgress
from .movie_resources import OscarMovieResource
from .services.movie_import import COLUMNS

CSV_PATH = Path(__file__).parent / 'data' / 'oscar_best_picture_ate_2025.csv'


def dataset(*rows):
    result = tablib.Dataset(headers=COLUMNS)
    for extra in rows:
        values = dict(name='Filme teste', release_year=2024, award_year=2025, award_edition=97,
                      group_name='Entretenimento', category_name='Oscar',
                      collection_slug='oscar-best-picture', collection_name='Oscar — Melhor Filme',
                      runtime_minutes='', poster_url='', watch_url='', description='')
        values.update(extra)
        result.append([values[key] for key in COLUMNS])
    return result


class MovieImportTests(TestCase):
    def setUp(self):
        self.baseline = {model: model.objects.count() for model in
                         (Group, Category, Activity, Movie, MovieCollection, MovieCollectionEntry)}

    def check_result(self, result):
        self.assertFalse(result.has_errors(), result.row_errors())
        self.assertFalse(result.has_validation_errors(), result.invalid_rows)

    def test_full_csv_preview_commit_reimport_preserves_progress_and_metadata(self):
        data = tablib.Dataset().load(CSV_PATH.read_text(), format='csv')
        self.assertEqual(len(data), 97)
        self.assertEqual(data[0][:4], ('Asas', '1927', '1929', '1'))
        self.assertEqual(data[-1][:4], ('Anora', '2024', '2025', '97'))
        for row in data.dict:
            self.assertIn('Brasil:', row['description'])
            self.assertIn('Fonte:', row['description'])
        preview = OscarMovieResource().import_data(data, dry_run=True)
        self.check_result(preview)
        for model in (Group, Category, Activity, Movie, MovieCollection, MovieCollectionEntry):
            self.assertEqual(model.objects.count(), self.baseline[model], model.__name__)
        self.check_result(OscarMovieResource().import_data(data))
        self.assertEqual(Movie.objects.count(), 97)
        self.assertEqual(MovieCollectionEntry.objects.count(), 97)
        self.assertEqual(MovieCollection.objects.count(), 1)
        movie = Movie.objects.get(activity__name='Anora')
        movie.active = False
        movie.runtime_minutes = 139
        movie.poster_url = 'https://example.org/poster.jpg'
        movie.save()
        progress = MovieProgress.objects.create(scope_key='test', movie=movie, status='watched', watched_at=timezone.now())
        self.check_result(OscarMovieResource().import_data(data))
        self.assertEqual(Movie.objects.count(), 97)
        self.assertEqual(Activity.objects.count(), 97)
        self.assertEqual(MovieCollectionEntry.objects.count(), 97)
        movie.refresh_from_db()
        progress.refresh_from_db()
        self.assertFalse(movie.active)
        self.assertEqual(movie.runtime_minutes, 139)
        self.assertEqual(movie.poster_url, 'https://example.org/poster.jpg')
        self.assertEqual(progress.version, 1)
        self.assertEqual(progress.status, 'watched')
        self.assertIsNotNone(progress.watched_at)
        self.assertEqual(movie.activity.duration, 60)
        self.assertIn('Amazon Prime Video', movie.activity.description)

    def test_translation_and_description_update_preserve_identity_and_progress(self):
        self.check_result(OscarMovieResource().import_data(dataset({'name': 'Wings'})))
        movie = Movie.objects.get()
        original_id = movie.pk
        progress = MovieProgress.objects.create(scope_key='test', movie=movie, status='watched',
                                               watched_at=timezone.now())
        description = 'Brasil: assinatura no Belas Artes à La Carte.'
        self.check_result(OscarMovieResource().import_data(dataset({'name': 'Asas', 'description': description})))
        movie.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(movie.pk, original_id)
        self.assertEqual(movie.activity.name, 'Asas')
        self.assertEqual(movie.activity.description, description)
        self.assertEqual(progress.movie_id, original_id)
        self.assertEqual(progress.status, 'watched')
        self.assertEqual(progress.version, 1)

    def test_blank_description_and_legacy_csv_preserve_existing_description(self):
        description = 'Descrição editorial existente'
        self.check_result(OscarMovieResource().import_data(dataset({'description': description})))
        self.check_result(OscarMovieResource().import_data(dataset({})))
        legacy = dataset({})
        del legacy['description']
        self.check_result(OscarMovieResource().import_data(legacy))
        self.assertEqual(Movie.objects.get().activity.description, description)

    def test_invalid_second_row_rolls_back_whole_file(self):
        result = OscarMovieResource().import_data(dataset({}, {'name': 'Outro', 'award_edition': 96,
                                                              'poster_url': 'file:///invalid'}))
        self.assertTrue(result.has_validation_errors())
        for model in (Group, Category, Activity, Movie, MovieCollection, MovieCollectionEntry):
            self.assertEqual(model.objects.count(), self.baseline[model])

    def test_duplicate_edition_rolls_back(self):
        result = OscarMovieResource().import_data(dataset({}, {'name': 'Outro'}))
        self.assertTrue(result.has_validation_errors())
        self.assertEqual(Movie.objects.count(), 0)

    def test_category_in_another_group_is_not_moved(self):
        group = Group.objects.create(name='Outro grupo')
        category = Category.objects.create(name='Oscar', group=group)
        result = OscarMovieResource().import_data(dataset({}))
        self.assertTrue(result.has_validation_errors())
        category.refresh_from_db()
        self.assertEqual(category.group_id, group.pk)
        self.assertEqual(Group.objects.count(), self.baseline[Group] + 1)

    def test_existing_manual_movie_is_linked_without_duplicate(self):
        self.check_result(OscarMovieResource().import_data(dataset({})))
        movie = Movie.objects.get()
        activity = movie.activity
        activity.external_source = activity.external_id = ''
        activity.save()
        self.check_result(OscarMovieResource().import_data(dataset({})))
        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(Activity.objects.count(), 1)

    def test_common_activity_requires_explicit_movie_link(self):
        group = Group.objects.create(name='Entretenimento')
        category = Category.objects.create(name='Oscar', group=group)
        activity = Activity.objects.create(name='Filme teste', category=category)
        result = OscarMovieResource().import_data(dataset({}))
        self.assertTrue(result.has_validation_errors())
        self.assertFalse(Movie.objects.exists())
        activity.refresh_from_db()
        self.assertEqual(activity.external_source, '')

    def test_bad_headers_and_years(self):
        data = dataset({})
        data.headers = ['unexpected'] + list(COLUMNS[1:])
        result = OscarMovieResource().import_data(data)
        self.assertTrue(result.has_errors())
        self.assertFalse(Activity.objects.exists())
        result = OscarMovieResource().import_data(dataset({'release_year': 'banana'}))
        self.assertTrue(result.has_validation_errors())
        self.assertFalse(Activity.objects.exists())

    def test_same_year_different_editions_allowed(self):
        self.check_result(OscarMovieResource().import_data(dataset(
            {'name': 'The Broadway Melody', 'award_year': 1930, 'release_year': 1929, 'award_edition': 2},
            {'name': 'All Quiet on the Western Front', 'award_year': 1930, 'release_year': 1930, 'award_edition': 3})))
        self.assertEqual(Movie.objects.count(), 2)

    def test_admin_permissions_and_preview_confirmation(self):
        user = get_user_model().objects.create_superuser('importer', 'importer@example.org', 'test-password')
        self.client.force_login(user)
        url = reverse('admin:pomodoro_movie_import')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = dataset({}).csv.encode()
        response = self.client.post(url, {
            'format': '0', 'resource': '0',
            'import_file': SimpleUploadedFile('movies.csv', data, content_type='text/csv'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Movie.objects.exists())
        confirm = response.context['confirm_form']
        response = self.client.post(reverse('admin:pomodoro_movie_process_import'), confirm.initial)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Movie.objects.count(), 1)
        staff = get_user_model().objects.create_user('limited', password='test-password', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='change_movie'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
