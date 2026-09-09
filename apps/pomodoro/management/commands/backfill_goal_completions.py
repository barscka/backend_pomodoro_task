import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.pomodoro.models import GoalCompletion, Schedule
from apps.pomodoro.services.goal_completions import completion_values, record_completion


class Command(BaseCommand):
    help = 'Carrega conclusões válidas para metas, preservando fatos existentes e o histórico original.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        if not 1 <= batch_size <= 10000:
            raise CommandError('batch-size deve estar entre 1 e 10000.')
        counts = Counter(scanned=0, eligible=0, inserted=0, existing=0)
        excluded = Counter()
        last_id = 0
        # Bound the run; newly created schedules are handled by the live service.
        upper_id = Schedule.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
        while last_id < upper_id:
            ids = list(Schedule.objects.filter(pk__gt=last_id, pk__lte=upper_id)
                       .order_by('pk').values_list('pk', flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                schedules = Schedule.objects.filter(pk__in=ids).order_by('pk')
                if not options['dry_run']:
                    # No nullable joins in FOR UPDATE; serialize with live completion.
                    schedules = schedules.select_for_update()
                for schedule in schedules:
                    counts['scanned'] += 1
                    if GoalCompletion.objects.filter(source_schedule_id=schedule.pk).exists():
                        counts['existing'] += 1
                        continue
                    _, reason = completion_values(schedule)
                    if reason:
                        excluded[reason] += 1
                        continue
                    counts['eligible'] += 1
                    if not options['dry_run']:
                        counts[record_completion(schedule)] += 1
            last_id = ids[-1]
        self.stdout.write(json.dumps({
            'dry_run': options['dry_run'], **counts, 'excluded': dict(excluded),
        }, sort_keys=True))
