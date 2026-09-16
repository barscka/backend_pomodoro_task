from django.core.management.base import BaseCommand
from django.db import transaction

from apps.pomodoro.models import GoalCompletion, History, Schedule


class Command(BaseCommand):
    help = 'Enriquece fatos legados recuperáveis sem alterar sua semântica de metas.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=200)

    def handle(self, *args, **options):
        updated = missing = 0
        qs = GoalCompletion.objects.filter(started_at__isnull=True).iterator(chunk_size=options['batch_size'])
        with transaction.atomic():
            for fact in qs:
                schedule = Schedule.objects.filter(pk=fact.source_schedule_id).select_related('activity').first()
                try:
                    history = schedule.execution_history if schedule else None
                except History.DoesNotExist:
                    history = None
                if not history or not history.end_time:
                    missing += 1
                    continue
                fact.activity_id_snapshot = schedule.activity_id
                fact.activity_name_snapshot = schedule.activity.name
                fact.started_at = history.start_time
                fact.duration_seconds = max(int((history.end_time-history.start_time).total_seconds()), 0)
                fact.execution_origin = schedule.execution_origin
                fact.save(update_fields=['activity_id_snapshot', 'activity_name_snapshot', 'started_at', 'duration_seconds', 'execution_origin'])
                updated += 1
            if options['dry_run']:
                transaction.set_rollback(True)
        self.stdout.write(f'updated={updated} missing={missing} dry_run={options["dry_run"]}')
