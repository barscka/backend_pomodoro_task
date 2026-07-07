from apps.pomodoro.models import get_default_category_id
from django.core.management.color import no_style
from django.db import migrations, models
import django.db.models.deletion


DEFAULT_CATEGORY_ID = 1
DEFAULT_CATEGORY_NAME = 'Todos'
DEFAULT_CATEGORY_DESCRIPTION = 'Categoria padrao para atividades sem classificacao especifica.'
DEFAULT_CATEGORY_COLOR = '#FFFFFF'
DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS = 2


def _get_default_group(Group):
    default_groups = list(Group.objects.filter(is_default=True).order_by('id'))
    if len(default_groups) > 1:
        raise RuntimeError('Existe mais de um grupo padrao com is_default=True.')
    if default_groups:
        return default_groups[0]

    group, _ = Group.objects.get_or_create(
        name='Todos',
        defaults={
            'description': 'Grupo padrao que mantem o comportamento atual.',
            'color': '#FFFFFF',
            'is_default': True,
        },
    )
    if not group.is_default:
        group.is_default = True
        group.save(update_fields=['is_default'])
    return group


def _next_category_id(Category):
    max_id = Category.objects.order_by('-id').values_list('id', flat=True).first() or 0
    return max_id + 1


def _create_default_category(Category, default_group, source=None):
    defaults = {
        'name': DEFAULT_CATEGORY_NAME,
        'description': DEFAULT_CATEGORY_DESCRIPTION,
        'color': DEFAULT_CATEGORY_COLOR,
        'max_daily_executions': DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS,
        'executions_today': 0,
        'group': default_group,
    }
    if source is not None:
        defaults.update(
            description=source.description or DEFAULT_CATEGORY_DESCRIPTION,
            color=source.color or DEFAULT_CATEGORY_COLOR,
            max_daily_executions=source.max_daily_executions or DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS,
            executions_today=source.executions_today,
            group_id=source.group_id or default_group.id,
        )
    return Category.objects.create(
        id=DEFAULT_CATEGORY_ID,
        **defaults,
    )


def ensure_default_category(apps, schema_editor):
    Category = apps.get_model('pomodoro', 'Category')
    Activity = apps.get_model('pomodoro', 'Activity')
    Group = apps.get_model('pomodoro', 'Group')

    default_group = _get_default_group(Group)
    current_default = Category.objects.filter(pk=DEFAULT_CATEGORY_ID).first()
    todos_elsewhere = Category.objects.filter(name=DEFAULT_CATEGORY_NAME).exclude(pk=DEFAULT_CATEGORY_ID).first()

    if current_default and current_default.name != DEFAULT_CATEGORY_NAME:
        new_id = _next_category_id(Category)
        Category.objects.create(
            id=new_id,
            name=current_default.name,
            description=current_default.description,
            color=current_default.color,
            max_daily_executions=current_default.max_daily_executions,
            executions_today=current_default.executions_today,
            group_id=current_default.group_id,
        )
        Activity.objects.filter(category_id=DEFAULT_CATEGORY_ID).update(category_id=new_id)
        current_default.delete()
        current_default = None

    if not current_default:
        current_default = _create_default_category(
            Category,
            default_group,
            source=todos_elsewhere,
        )

    if todos_elsewhere and todos_elsewhere.pk != current_default.pk:
        Activity.objects.filter(category_id=todos_elsewhere.pk).update(category_id=current_default.pk)
        todos_elsewhere.delete()

    current_default.name = DEFAULT_CATEGORY_NAME
    current_default.description = current_default.description or DEFAULT_CATEGORY_DESCRIPTION
    current_default.color = current_default.color or DEFAULT_CATEGORY_COLOR
    current_default.group_id = default_group.id
    if not current_default.max_daily_executions:
        current_default.max_daily_executions = DEFAULT_CATEGORY_MAX_DAILY_EXECUTIONS
    current_default.save(
        update_fields=['name', 'description', 'color', 'group', 'max_daily_executions']
    )

    Activity.objects.filter(category__isnull=True).update(category_id=DEFAULT_CATEGORY_ID)

    sequence_sql = schema_editor.connection.ops.sequence_reset_sql(
        no_style(),
        [Category],
    )
    for sql in sequence_sql:
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ('pomodoro', '0011_schedule_completed_at_schedule_expected_end_at_and_more'),
    ]

    operations = [
        migrations.RunPython(ensure_default_category, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='activity',
            name='category',
            field=models.ForeignKey(
                default=get_default_category_id,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='activities',
                to='pomodoro.category',
            ),
        ),
    ]
