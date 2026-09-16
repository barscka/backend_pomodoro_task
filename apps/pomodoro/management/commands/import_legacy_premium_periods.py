from django.core.management.base import BaseCommand
from django.db import transaction

from apps.pomodoro.models import Activity, PremiumPeriod


class Command(BaseCommand):
    help = 'Importa vigências premium legadas de forma idempotente.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=200)
        parser.add_argument('--kind', choices=['paid', 'focus'], default='focus')

    def handle(self, *args, **options):
        created = skipped = invalid = 0
        qs = Activity.objects.filter(premium_from__isnull=False, premium_until__isnull=False).iterator(chunk_size=options['batch_size'])
        with transaction.atomic():
            for activity in qs:
                if activity.premium_from > activity.premium_until:
                    invalid += 1
                    continue
                _, was_created = PremiumPeriod.objects.get_or_create(
                    activity=activity, starts_on=activity.premium_from, ends_on=activity.premium_until,
                    defaults={'kind': options['kind'], 'title': activity.name, 'source': 'legacy_inferred'},
                )
                created += int(was_created)
                skipped += int(not was_created)
            if options['dry_run']:
                transaction.set_rollback(True)
        self.stdout.write(f'created={created} skipped={skipped} invalid={invalid} dry_run={options["dry_run"]}')
