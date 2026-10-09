"""Recurso CSV transacional: Activity + Movie + entrada da coleção."""
from django.core.exceptions import ValidationError
from import_export import fields, resources

from .models import Movie
from .services import movie_import


class OscarMovieResource(resources.ModelResource):
    name = fields.Field(column_name='name', attribute='activity__name', readonly=True)
    release_year = fields.Field(column_name='release_year', attribute='release_year', readonly=True)
    award_year = fields.Field(column_name='award_year', readonly=True)
    award_edition = fields.Field(column_name='award_edition', readonly=True)
    group_name = fields.Field(column_name='group_name', attribute='activity__category__group__name', readonly=True)
    category_name = fields.Field(column_name='category_name', attribute='activity__category__name', readonly=True)
    collection_slug = fields.Field(column_name='collection_slug', readonly=True)
    collection_name = fields.Field(column_name='collection_name', readonly=True)
    runtime_minutes = fields.Field(attribute='runtime_minutes', readonly=True)
    poster_url = fields.Field(attribute='poster_url', readonly=True)
    watch_url = fields.Field(attribute='watch_url', readonly=True)

    class Meta:
        model = Movie
        fields = movie_import.COLUMNS
        import_order = movie_import.COLUMNS
        export_order = movie_import.COLUMNS
        import_id_fields = ()
        use_transactions = True
        clean_model_instances = True
        skip_unchanged = False

    def import_data(self, dataset, dry_run=False, raise_errors=False, **kwargs):
        kwargs['use_transactions'] = True
        kwargs['rollback_on_validation_errors'] = True
        return super().import_data(dataset, dry_run=dry_run, raise_errors=raise_errors, **kwargs)

    def before_import(self, dataset, **kwargs):
        headers = dataset.headers or []
        missing = set(movie_import.REQUIRED) - set(headers)
        unknown = set(headers) - set(movie_import.COLUMNS)
        if missing or unknown or len(headers) != len(set(headers)):
            raise ValidationError(f'Cabeçalhos inválidos. Ausentes: {sorted(missing)}; desconhecidos: {sorted(unknown)}.')
        self._seen = set()

    def before_import_row(self, row, **kwargs):
        movie_import.validate_row(row)
        key = movie_import.identity(row)
        if key in self._seen:
            raise ValidationError({'award_edition': 'Edição repetida no arquivo.'})
        self._seen.add(key)

    def get_instance(self, instance_loader, row):
        return movie_import.find_movie(row)

    def import_instance(self, instance, row, **kwargs):
        movie_import.prepare_movie(instance, row)

    def after_save_instance(self, instance, row, **kwargs):
        movie_import.save_entry(instance)

    def _entry(self, movie):
        if not movie.pk:
            return None
        return movie.entries.filter(collection__slug=movie_import.SOURCE).select_related('collection').first()

    def dehydrate_award_year(self, movie):
        entry = self._entry(movie)
        return entry.award_year if entry else ''

    def dehydrate_award_edition(self, movie):
        entry = self._entry(movie)
        return entry.award_edition if entry else ''

    def dehydrate_collection_slug(self, movie):
        entry = self._entry(movie)
        return entry.collection.slug if entry else ''

    def dehydrate_collection_name(self, movie):
        entry = self._entry(movie)
        return entry.collection.name if entry else ''
