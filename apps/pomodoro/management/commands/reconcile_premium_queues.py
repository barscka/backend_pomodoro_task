import json
import logging

from django.core.management.base import BaseCommand, CommandError

from apps.pomodoro.services.activity_queue_reconciliation import (
    reconcile_all_premium_queues,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reconcilia a prioridade premium em todas as filas normais ativas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Calcula a reconciliacao e reverte todas as escritas.',
        )

    def handle(self, *args, **options):
        summary = reconcile_all_premium_queues(dry_run=options['dry_run'])
        payload = {'dry_run': options['dry_run'], **summary.as_dict()}
        self.stdout.write(json.dumps(payload, sort_keys=True))
        if summary.errors:
            logger.error('Falha na reconciliacao de filas premium', extra=payload)
            raise CommandError(
                f'Reconciliacao concluida com erro em {summary.errors} fila(s).'
            )
