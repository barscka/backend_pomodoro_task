import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.pomodoro.jobs.movie_import import process_next
from apps.pomodoro.models import MovieImportJob


class Command(BaseCommand):
    help = 'Processa prévias e importações de filmes fora das requisições do Admin.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Processa no máximo um trabalho e encerra.')
        parser.add_argument('--recover-interrupted', action='store_true',
                            help='Com os workers parados, marca trabalhos interrompidos como falha e encerra.')

    def handle(self, *args, **options):
        if options['recover_interrupted']:
            count = MovieImportJob.objects.filter(status__in=['previewing', 'importing']).update(
                status=MovieImportJob.Status.FAILED,
                report={'errors': ['Processamento interrompido. Envie novamente o CSV para gerar uma nova prévia.']},
                updated_at=timezone.now())
            self.stdout.write(f'{count} trabalhos interrompidos marcados como falha.')
            return
        self.stdout.write('Worker de importação de filmes iniciado.')
        try:
            while True:
                close_old_connections()
                worked = process_next()
                if options['once']:
                    return
                if not worked:
                    time.sleep(2)
        except KeyboardInterrupt:
            self.stdout.write('Worker encerrado.')
