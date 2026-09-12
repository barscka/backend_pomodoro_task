import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.pomodoro.models import ActivityQueueItem, GoalActivitySkip
from apps.pomodoro.services.goal_activity_skips import record_activity_skip, skip_values


class Command(BaseCommand):
    help = 'Carrega fatos históricos de pulo sem alterar fatos já existentes.'

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
        source = ActivityQueueItem.objects.filter(state=ActivityQueueItem.STATE_SKIPPED)
        upper_id = source.order_by('-pk').values_list('pk', flat=True).first() or 0
        while last_id < upper_id:
            ids = list(source.filter(pk__gt=last_id, pk__lte=upper_id)
                       .order_by('pk').values_list('pk', flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                items = ActivityQueueItem.objects.filter(pk__in=ids).select_related(
                    'queue', 'activity__category__group',
                ).order_by('pk')
                if not options['dry_run']:
                    items = items.select_for_update()
                for item in items:
                    counts['scanned'] += 1
                    if GoalActivitySkip.objects.filter(source_queue_item_id=item.pk).exists():
                        counts['existing'] += 1
                        continue
                    _, reason = skip_values(item)
                    if reason:
                        excluded[reason] += 1
                        continue
                    counts['eligible'] += 1
                    if not options['dry_run']:
                        counts[record_activity_skip(item, context_source='legacy_current')] += 1
            last_id = ids[-1]
        self.stdout.write(json.dumps({
            'dry_run': options['dry_run'], **counts, 'excluded': dict(excluded),
        }, sort_keys=True))
