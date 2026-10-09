from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .models import MovieImportJob
from .services.movie_import_jobs import can_import, confirm, enqueue_preview, parse_csv


class CSVUploadForm(forms.Form):
    import_file = forms.FileField(label='CSV dos filmes')


class AsyncMovieImportAdminMixin:
    def has_import_permission(self, request):
        return can_import(request.user)

    def get_urls(self):
        return [path('import/jobs/<uuid:job_id>/', self.admin_site.admin_view(self.import_job),
                     name='pomodoro_movie_import_job')] + super().get_urls()

    def import_context(self, request, **extra):
        return {**self.admin_site.each_context(request), 'opts': self.model._meta,
                'title': 'Importação de filmes', **extra}

    @method_decorator(csrf_protect)
    def import_action(self, request, **kwargs):
        if not self.has_import_permission(request):
            raise PermissionDenied
        if request.method not in ('GET', 'POST'):
            return HttpResponseNotAllowed(['GET', 'POST'])
        form = CSVUploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            try:
                job = enqueue_preview(request.user, form.cleaned_data['import_file'])
            except ValidationError as error:
                form.add_error('import_file', error)
            else:
                return redirect(reverse('admin:pomodoro_movie_import_job', args=[job.pk]))
        jobs = MovieImportJob.objects.filter(requested_by=request.user).defer('csv_text', 'report')[:20]
        return TemplateResponse(request, 'admin/pomodoro/movie_import/upload.html',
                                self.import_context(request, form=form, jobs=jobs))

    @method_decorator(csrf_protect)
    def import_job(self, request, job_id):
        if not self.has_import_permission(request):
            raise PermissionDenied
        jobs = MovieImportJob.objects.all()
        if not request.user.is_superuser:
            jobs = jobs.filter(requested_by=request.user)
        job = get_object_or_404(jobs, pk=job_id)
        if request.method == 'POST':
            confirm(job)
            return redirect(reverse('admin:pomodoro_movie_import_job', args=[job.pk]))
        if request.method != 'GET':
            return HttpResponseNotAllowed(['GET', 'POST'])
        pending = job.status in ('queued_preview', 'previewing', 'queued_import', 'importing')
        data = parse_csv(job.csv_text) if job.status == MovieImportJob.Status.READY else None
        return TemplateResponse(request, 'admin/pomodoro/movie_import/job.html',
                                self.import_context(request, job=job, pending=pending, data=data))

    def process_import(self, request, **kwargs):
        # Desabilita o antigo endpoint síncrono de confirmação.
        return HttpResponseNotAllowed(['GET'])
