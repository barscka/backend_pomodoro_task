"""Execução isolada da fila; a gravação e o resultado final usam a mesma transação."""
import logging

from django.db import transaction
from django.utils import timezone

from ..models import MovieImportJob
from ..movie_resources import OscarMovieResource
from ..services.movie_import_jobs import can_import, parse_csv

logger = logging.getLogger(__name__)
S = MovieImportJob.Status


def result_report(result):
    errors = [str(error.error) for error in result.base_errors]
    errors.extend(f'Linha {number}: {error.error}' for number, row_errors in result.row_errors() for error in row_errors)
    errors.extend(f'Linha {row.number}: {row.error}' for row in result.invalid_rows)
    return {'totals': dict(result.totals), 'total_rows': result.total_rows, 'errors': errors}


def process_next():
    job = MovieImportJob.objects.filter(status__in=[S.QUEUED_PREVIEW, S.QUEUED_IMPORT]).order_by('created_at').first()
    if job is None:
        return False
    preview = job.status == S.QUEUED_PREVIEW
    running = S.PREVIEWING if preview else S.IMPORTING
    claimed = MovieImportJob.objects.filter(pk=job.pk, status=job.status).update(
        status=running, updated_at=timezone.now())
    if not claimed:
        return True
    try:
        with transaction.atomic():
            # Reavaliar permissões antes de cada etapa, inclusive após confirmação.
            if not can_import(job.requested_by):
                raise PermissionError('O solicitante não possui mais permissão para importar.')
            result = OscarMovieResource().import_data(parse_csv(job.csv_text), dry_run=preview)
            report = result_report(result)
            status = S.FAILED if result.has_errors() or result.has_validation_errors() else (S.READY if preview else S.DONE)
            MovieImportJob.objects.filter(pk=job.pk, status=running).update(
                status=status, report=report, updated_at=timezone.now())
    except Exception:
        logger.exception('Falha no processamento da importação de filmes %s', job.pk)
        MovieImportJob.objects.filter(pk=job.pk, status=running).update(
            status=S.FAILED, report={'errors': ['Falha ao processar. Consulte os logs do worker e envie o CSV novamente.']},
            updated_at=timezone.now())
    return True
