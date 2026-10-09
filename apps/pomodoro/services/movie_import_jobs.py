"""Fila durável para prévia e confirmação, sem importar dentro da requisição HTTP."""
import csv
import io
from itertools import islice

import tablib
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from ..models import MovieImportJob

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000


def can_import(user):
    models = ('movie', 'activity', 'group', 'category', 'moviecollection', 'moviecollectionentry')
    return user.is_active and user.is_staff and all(
        user.has_perm(f'pomodoro.{action}_{model}')
        for model in models for action in ('add', 'change'))


def parse_csv(text):
    # Limita dimensões antes de construir o Dataset; somente CSV UTF-8.
    rows = list(islice(csv.reader(io.StringIO(text), strict=True), MAX_ROWS + 2))
    if len(rows) < 2 or len(rows) > MAX_ROWS + 1:
        raise ValidationError(f'Envie de 1 a {MAX_ROWS} filmes.')
    if not rows[0] or len(rows[0]) > 12 or any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValidationError('CSV com quantidade de colunas inválida ou linhas incompletas.')
    data = tablib.Dataset(*rows[1:], headers=rows[0])
    return data


def enqueue_preview(user, upload):
    from ..movie_resources import OscarMovieResource
    if not can_import(user):
        raise PermissionDenied
    if upload.size > MAX_BYTES:
        raise ValidationError('O CSV deve ter no máximo 2 MB.')
    try:
        content = upload.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValidationError('O CSV deve ter no máximo 2 MB.')
        text = content.decode('utf-8-sig')
        data = parse_csv(text)
    except (UnicodeDecodeError, csv.Error, ValueError) as error:
        raise ValidationError('CSV inválido. Use UTF-8 e vírgulas como separador.') from error
    OscarMovieResource()._validate_headers(data)
    return MovieImportJob.objects.create(requested_by=user, filename=upload.name[:255], csv_text=text)


def confirm(job):
    return MovieImportJob.objects.filter(pk=job.pk, status=MovieImportJob.Status.READY).update(
        status=MovieImportJob.Status.QUEUED_IMPORT, updated_at=timezone.now()) == 1
