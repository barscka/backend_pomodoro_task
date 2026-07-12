from django.db import migrations, models
import django.db.models.deletion
import apps.pomodoro.models


def normalize_legacy_queues(apps, schema_editor):
    Group = apps.get_model('pomodoro', 'Group')
    ActivityQueue = apps.get_model('pomodoro', 'ActivityQueue')
    Schedule = apps.get_model('pomodoro', 'Schedule')
    ActivityQueueItem = apps.get_model('pomodoro', 'ActivityQueueItem')
    ActivityPreferenceEvent = apps.get_model('pomodoro', 'ActivityPreferenceEvent')

    default_group = Group.objects.filter(is_default=True).order_by('id').first()
    if default_group is None:
        default_group, _ = Group.objects.get_or_create(
            name='Todos',
            defaults={
                'description': 'Grupo padrao que mantem o comportamento atual.',
                'color': '#FFFFFF',
                'max_daily_minutes': 0,
            },
        )
        default_group.is_default = True
        default_group.save(update_fields=['is_default'])
    Group.objects.exclude(pk=default_group.pk).filter(is_default=True).update(is_default=False)

    ActivityQueue.objects.filter(group__isnull=True).update(group=default_group)

    active_pairs = (
        ActivityQueue.objects.filter(state='active')
        .values_list('scope_key', 'group_id')
        .distinct()
    )
    for scope_key, group_id in active_pairs.iterator():
        queues = list(
            ActivityQueue.objects.filter(
                scope_key=scope_key,
                group_id=group_id,
                state='active',
            ).order_by('-created_at', '-id')
        )
        if len(queues) < 2:
            continue

        open_queue_id = (
            Schedule.objects.filter(
                scope_key=scope_key,
                state__in=['preparing', 'running'],
                queue_item__queue_id__in=[queue.id for queue in queues],
            )
            .values_list('queue_item__queue_id', flat=True)
            .first()
        )
        keep_id = open_queue_id or queues[0].id
        for queue in queues:
            if queue.id == keep_id:
                continue
            queue.state = 'cancelled'
            queue.closed_at = queue.closed_at or queue.created_at
            queue.save(update_fields=['state', 'closed_at'])

    duplicates = (
        ActivityQueueItem.objects.values('queue_id', 'activity_id')
        .annotate(amount=models.Count('id'))
        .filter(amount__gt=1)
    )
    for duplicate in duplicates.iterator():
        items = list(
            ActivityQueueItem.objects.filter(
                queue_id=duplicate['queue_id'],
                activity_id=duplicate['activity_id'],
            ).order_by('position', 'id')
        )
        original_queue = ActivityQueue.objects.get(pk=duplicate['queue_id'])
        for item in items[1:]:
            archive = ActivityQueue.objects.create(
                group_id=original_queue.group_id,
                scope_key=original_queue.scope_key,
                state='cancelled',
                mode=original_queue.mode,
                pool_number=original_queue.pool_number,
                pool_size=1,
                consumed_count=1 if item.state in ['completed', 'skipped'] else 0,
                skip_locked=original_queue.skip_locked,
                closed_at=original_queue.closed_at or original_queue.created_at,
            )
            item.queue_id = archive.id
            item.position = 1
            item.save(update_fields=['queue', 'position'])
            ActivityPreferenceEvent.objects.filter(queue_item_id=item.id).update(queue_id=archive.id)
        original_queue.pool_size = ActivityQueueItem.objects.filter(queue=original_queue).count()
        original_queue.save(update_fields=['pool_size'])


class Migration(migrations.Migration):
    dependencies = [('pomodoro', '0013_remove_schedule_daily_unique')]

    operations = [
        migrations.AddField(
            model_name='group',
            name='max_daily_minutes',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='activityqueue',
            name='source_queue',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='review_queue',
                to='pomodoro.activityqueue',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='activityqueue',
            name='unique_active_queue_per_scope',
        ),
        migrations.RunPython(normalize_legacy_queues, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.AlterField(
                    model_name='activityqueue',
                    name='group',
                    field=models.ForeignKey(
                        default=1,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='activity_queues',
                        to='pomodoro.group',
                    ),
                    preserve_default=False,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='activityqueue',
                    name='group',
                    field=models.ForeignKey(
                        default=apps.pomodoro.models.get_default_group_id,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='activity_queues',
                        to='pomodoro.group',
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name='activityqueue',
            constraint=models.UniqueConstraint(
                condition=models.Q(('state', 'active')),
                fields=('scope_key', 'group'),
                name='unique_active_queue_per_scope_group',
            ),
        ),
        migrations.AddConstraint(
            model_name='activityqueueitem',
            constraint=models.UniqueConstraint(
                fields=('queue', 'activity'),
                name='unique_activity_per_queue',
            ),
        ),
        migrations.AddIndex(
            model_name='activityqueue',
            index=models.Index(fields=['scope_key', 'group', 'state'], name='queue_scope_group_state_idx'),
        ),
        migrations.AddIndex(
            model_name='activityqueue',
            index=models.Index(fields=['source_queue', 'mode'], name='queue_source_mode_idx'),
        ),
        migrations.AddIndex(
            model_name='activityqueueitem',
            index=models.Index(fields=['queue', 'state', 'position'], name='queue_item_state_pos_idx'),
        ),
    ]
