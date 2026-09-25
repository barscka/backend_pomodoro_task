import json

from django.core.management.base import BaseCommand, CommandError

from apps.pomodoro.services.retrogames_catalog_import import (
    CatalogImportError,
    DEFAULT_CATALOG_PATH,
    import_catalog,
)


class Command(BaseCommand):
    help = 'Importa de forma idempotente o catálogo editorial versionado de RetroGames.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Planeja sem gravar no banco.')
        parser.add_argument('--deactivate-missing', action='store_true', help='Desativa jogos gerenciados ausentes da fonte.')
        parser.add_argument('--inventory-file', help='Compara com um inventário local, sem alterar a fonte editorial.')
        parser.add_argument('--catalog-file', default=str(DEFAULT_CATALOG_PATH), help='Arquivo de catálogo (útil para testes).')
        parser.add_argument('--format', choices=('human', 'json'), default='human')

    def handle(self, *args, **options):
        try:
            report = import_catalog(
                catalog_path=options['catalog_file'],
                dry_run=options['dry_run'],
                deactivate_missing=options['deactivate_missing'],
                inventory_path=options['inventory_file'],
            )
        except CatalogImportError as exc:
            raise CommandError(str(exc)) from exc

        payload = report.as_dict()
        if options['format'] == 'json':
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return

        self.stdout.write(
            f"Catálogo RetroGames {report.catalog_version} ({'simulação' if report.dry_run else 'aplicado'})"
        )
        labels = (
            ('Grupo', report.group),
            ('Gerações', report.generations),
            ('Plataformas', report.platforms),
            ('Jogos', report.games),
        )
        for label, stats in labels:
            self.stdout.write(
                f'{label}: criados={stats.created}, atualizados={stats.updated}, '
                f'inalterados={stats.unchanged}, desativados={stats.deactivated}, órfãos={stats.orphaned}'
            )
        if report.inventory:
            self.stdout.write('Inventário: ' + ', '.join(f'{key}={value}' for key, value in report.inventory.items()))
        if report.changes:
            self.stdout.write('Detalhes:')
            for change in report.changes:
                self.stdout.write(f'  - {change}')
        if report.warnings:
            self.stdout.write('Avisos:')
            for warning in report.warnings:
                self.stdout.write(f'  - {warning}')
