from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from .jobs.movie_import import process_next
from .models import Activity, Movie, MovieImportJob
from .services.movie_import_jobs import confirm, enqueue_preview, parse_csv
from .test_movie_import import CSV_PATH, dataset


class MovieImportJobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('worker-admin', 'a@example.com', 'test-password')
        self.client.force_login(self.user)

    def enqueue(self, data=None):
        return enqueue_preview(self.user, SimpleUploadedFile('movies.csv', (data or dataset({})).csv.encode()))

    def test_full_csv_is_queued_without_running_import_and_confirm_is_idempotent(self):
        with patch('apps.pomodoro.movie_resources.OscarMovieResource.import_data') as importer:
            job = enqueue_preview(self.user, SimpleUploadedFile('movies.csv', CSV_PATH.read_bytes()))
            importer.assert_not_called()
        call_command('process_movie_imports', once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'ready')
        self.assertEqual(job.report['total_rows'], 97)
        self.assertFalse(Movie.objects.exists())
        self.assertTrue(confirm(job))
        self.assertFalse(confirm(job))
        call_command('process_movie_imports', once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')
        self.assertEqual(Movie.objects.count(), 97)
        self.assertFalse(process_next())

    def test_bad_row_preview_fails_and_cannot_be_confirmed(self):
        job = self.enqueue(dataset({}, {'name': 'Invalid', 'award_edition': 96, 'release_year': 'banana'}))
        process_next()
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertIn('Linha 2', job.report['errors'][0])
        self.assertFalse(confirm(job))
        self.assertFalse(Activity.objects.exists())

    def test_final_failure_rolls_back_file_and_records_failure(self):
        job = self.enqueue(dataset({}, {'name': 'Outro', 'award_edition': 96}))
        process_next()
        self.assertTrue(confirm(job))
        from .services.movie_import import save_entry
        def fail_second(movie):
            if movie.activity.name == 'Outro':
                raise RuntimeError('simulated failure')
            save_entry(movie)
        with patch('apps.pomodoro.services.movie_import.save_entry', side_effect=fail_second):
            process_next()
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertTrue(job.report['errors'])
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(Movie.objects.exists())

    def test_worker_rechecks_revoked_permissions(self):
        job = self.enqueue()
        process_next()
        self.assertTrue(confirm(job))
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertLogs('apps.pomodoro.jobs.movie_import', level='ERROR'):
            process_next()
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertFalse(Movie.objects.exists())

    def test_upload_validation_and_job_visibility(self):
        for content in (b'wrong,headers\na,b', b'\xff', b'name\n', b'x' * (2 * 1024 * 1024 + 1)):
            with self.assertRaises(ValidationError):
                enqueue_preview(self.user, SimpleUploadedFile('bad.csv', content))
        self.assertFalse(MovieImportJob.objects.exists())
        job = self.enqueue()
        url = reverse('admin:pomodoro_movie_import_job', args=[job.pk])
        other = get_user_model().objects.create_user('other', password='pw', is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        other.user_permissions.add(*Permission.objects.filter(content_type__app_label='pomodoro'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        with self.assertRaises(ValidationError):
            parse_csv('name\n' + 'film\n' * 1001)

    def test_confirmation_requires_csrf_and_waiting_job_cannot_be_confirmed(self):
        job = self.enqueue()
        url = reverse('admin:pomodoro_movie_import_job', args=[job.pk])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued_preview')
        self.assertEqual(self.client.post(reverse('admin:pomodoro_movie_process_import')).status_code, 405)

    def test_recover_only_interrupted_jobs_without_importing(self):
        interrupted = self.enqueue()
        queued = self.enqueue()
        MovieImportJob.objects.filter(pk=interrupted.pk).update(status='importing')
        call_command('process_movie_imports', recover_interrupted=True)
        interrupted.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(interrupted.status, 'failed')
        self.assertEqual(queued.status, 'queued_preview')
        self.assertFalse(Movie.objects.exists())
