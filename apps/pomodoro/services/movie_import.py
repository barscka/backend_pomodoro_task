"""Cadastro editorial de filmes a partir de linhas validadas do import-export."""
from django.core.exceptions import ValidationError

from apps.pomodoro.models import Activity, Category, Group, Movie, MovieCollection, MovieCollectionEntry

SOURCE = 'oscar-best-picture'
REQUIRED = ('name', 'release_year', 'award_year', 'award_edition', 'group_name',
            'category_name', 'collection_slug', 'collection_name')
OPTIONAL = ('runtime_minutes', 'poster_url', 'watch_url', 'description')
COLUMNS = REQUIRED + OPTIONAL


def validate_row(row):
    for key in REQUIRED:
        if not str(row.get(key, '') or '').strip():
            raise ValidationError({key: 'Campo obrigatório.'})
    for key in COLUMNS:
        row[key] = str(row.get(key, '') or '').strip()
    for key in ('release_year', 'award_year', 'award_edition', 'runtime_minutes'):
        if row[key]:
            try:
                value = int(row[key])
            except ValueError:
                raise ValidationError({key: 'Informe um inteiro.'})
            low, high = ((1888, 2100) if key.endswith('year') else
                         (1, 1440) if key == 'runtime_minutes' else (1, 32767))
            if not low <= value <= high:
                raise ValidationError({key: f'Valor entre {low} e {high}.'})
            row[key] = value
    if row['collection_slug'] != SOURCE:
        raise ValidationError({'collection_slug': 'Este recurso importa a coleção oscar-best-picture.'})
    return row


def identity(row):
    return str(row['award_edition'])


def find_movie(row):
    activity = Activity.objects.filter(external_source=SOURCE, external_id=identity(row)).first()
    if activity:
        if not Movie.objects.filter(activity=activity).exists():
            raise ValidationError('A identidade externa já pertence a uma atividade sem Movie.')
        return Movie.objects.get(activity=activity)
    matches = Movie.objects.filter(activity__name=row['name'], release_year=row['release_year'],
                                   activity__category__name=row['category_name'],
                                   activity__category__group__name=row['group_name'])
    if matches.count() > 1:
        raise ValidationError('Mais de um filme existente corresponde ao título e ano.')
    movie = matches.first()
    if movie and movie.activity.external_source and (
        movie.activity.external_source != SOURCE or movie.activity.external_id != identity(row)
    ):
        raise ValidationError('O filme correspondente já tem outra identidade externa.')
    return movie


def prepare_movie(movie, row):
    group, _ = Group.objects.get_or_create(name=row['group_name'])
    group.full_clean()
    category, _ = Category.objects.get_or_create(name=row['category_name'], defaults={'group': group})
    if category.group_id != group.pk:
        raise ValidationError({'category_name': 'A categoria existente pertence a outro grupo.'})
    category.full_clean()
    collection, _ = MovieCollection.objects.get_or_create(
        slug=row['collection_slug'], defaults={'name': row['collection_name'], 'category': category})
    if collection.category_id != category.pk or collection.name != row['collection_name']:
        raise ValidationError({'collection_slug': 'Nome/categoria incompatíveis com a coleção existente.'})
    collection.full_clean()
    if movie.pk:
        activity = movie.activity
        if activity.category_id != category.pk:
            raise ValidationError('O filme existente pertence a outra categoria.')
    else:
        # Não converter atividade comum silenciosamente: pode ter sessões/fila próprias.
        if Activity.objects.filter(name=row['name'], category=category).exists():
            raise ValidationError('Já existe Activity com esse nome; vincule Movie manualmente antes de importar.')
        activity = Activity(category=category)
    activity.name = row['name']
    if row['description']:
        activity.description = row['description']
    activity.external_source = SOURCE
    activity.external_id = identity(row)
    activity.full_clean()
    activity.save()
    movie.activity = activity
    movie.release_year = row['release_year']
    # Células vazias não apagam enriquecimento manual nem reativam filmes.
    for key in OPTIONAL:
        if key != 'description' and row[key] != '':
            setattr(movie, key, row[key])
    movie._import_collection = collection
    movie._import_entry_values = {'award_year': row['award_year'], 'award_edition': row['award_edition']}


def save_entry(movie):
    entry, _ = MovieCollectionEntry.objects.get_or_create(
        movie=movie, collection=movie._import_collection, defaults=movie._import_entry_values)
    for key, value in movie._import_entry_values.items():
        setattr(entry, key, value)
    entry.full_clean()
    entry.save()
