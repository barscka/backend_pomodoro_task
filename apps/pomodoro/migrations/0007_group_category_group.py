from django.db import migrations, models
import django.db.models.deletion


def create_default_group(apps, schema_editor):
    Group = apps.get_model('pomodoro', 'Group')
    Category = apps.get_model('pomodoro', 'Category')

    default_group, _ = Group.objects.get_or_create(
        name='Todos',
        defaults={
            'description': 'Grupo padrao que mantem o comportamento atual.',
            'color': '#FFFFFF',
            'is_default': True,
        },
    )

    if not default_group.is_default:
        default_group.is_default = True
        default_group.save(update_fields=['is_default'])

    Category.objects.filter(group__isnull=True).update(group=default_group)


class Migration(migrations.Migration):

    dependencies = [
        ('pomodoro', '0006_schedule_end_time'),
    ]

    operations = [
        migrations.CreateModel(
            name='Group',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('color', models.CharField(default='#FFFFFF', max_length=7)),
                ('is_default', models.BooleanField(default=False)),
            ],
            options={
                'verbose_name': 'Group',
                'verbose_name_plural': 'Groups',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='category',
            name='group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='categories',
                to='pomodoro.group',
            ),
        ),
        migrations.RunPython(create_default_group, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='category',
            name='group',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='categories',
                to='pomodoro.group',
            ),
        ),
    ]
