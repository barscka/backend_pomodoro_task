"""Catálogo editorial e progresso cinematográfico, independente de Pomodoro."""
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, URLValidator
from django.db import models, transaction
from django.db.models import Q

YEAR = [MinValueValidator(1888), MaxValueValidator(2100)]
WEB = [URLValidator(schemes=['http', 'https'])]


class MovieCollection(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    category = models.ForeignKey('Category', on_delete=models.PROTECT, related_name='movie_collections')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.pk and self.entries.exclude(movie__activity__category_id=self.category_id).exists():
            raise ValidationError({'category': 'A categoria deve ser compatível com todos os filmes.'})

    def __str__(self):
        return self.name


class Movie(models.Model):
    activity = models.OneToOneField('Activity', on_delete=models.PROTECT, related_name='movie')
    release_year = models.PositiveSmallIntegerField(validators=YEAR)
    runtime_minutes = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(1440)])
    poster_url = models.URLField(max_length=1000, null=True, blank=True, validators=WEB)
    watch_url = models.URLField(max_length=1000, null=True, blank=True, validators=WEB)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(release_year__gte=1888, release_year__lte=2100), name='movie_release_year_range'),
            models.CheckConstraint(condition=Q(runtime_minutes__isnull=True) | Q(runtime_minutes__gte=1, runtime_minutes__lte=1440), name='movie_runtime_range'),
        ]

    def clean(self):
        if self.pk and self.entries.exclude(collection__category_id=self.activity.category_id).exists():
            raise ValidationError({'activity': 'A categoria deve ser compatível com as coleções.'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Cadastro direto e inline também removem reservas futuras das filas.
        from .services.activity_queue_reconciliation import reconcile_activity
        reconcile_activity(self.activity)

    def __str__(self):
        return self.activity.name


class MovieCollectionEntry(models.Model):
    collection = models.ForeignKey(MovieCollection, on_delete=models.PROTECT, related_name='entries')
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT, related_name='entries')
    award_year = models.PositiveSmallIntegerField(validators=YEAR)
    award_edition = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['award_year', 'movie__release_year', 'movie__activity__name', 'movie_id']
        constraints = [
            models.UniqueConstraint(fields=['collection', 'movie'], name='movie_entry_collection_unique'),
            models.CheckConstraint(condition=Q(award_year__gte=1888, award_year__lte=2100), name='movie_award_year_range'),
            models.CheckConstraint(condition=Q(award_edition__isnull=True) | Q(award_edition__gte=1), name='movie_award_edition_positive'),
        ]
        indexes = [models.Index(fields=['collection', 'award_year'], name='movie_entry_award_idx')]

    def clean(self):
        if self.collection_id and self.movie_id and self.collection.category_id != self.movie.activity.category_id:
            raise ValidationError({'movie': 'O filme deve pertencer à categoria da coleção.'})


class MovieProgress(models.Model):
    scope_key = models.CharField(max_length=64)
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT, related_name='progress_records')
    status = models.CharField(max_length=12, choices=[(s, s) for s in ('unwatched', 'watching', 'watched')], default='unwatched')
    started_at = models.DateTimeField(null=True, blank=True)
    watched_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['scope_key', 'movie'], name='movie_progress_scope_unique'),
            models.CheckConstraint(condition=Q(version__gte=1), name='movie_progress_version_positive'),
            models.CheckConstraint(condition=(Q(status='unwatched', started_at__isnull=True, watched_at__isnull=True) | Q(status='watching', started_at__isnull=False, watched_at__isnull=True) | Q(status='watched', watched_at__isnull=False)), name='movie_progress_status_dates'),
        ]
        indexes = [models.Index(fields=['scope_key', 'status'], name='movie_progress_status_idx')]


class MovieDrawState(models.Model):
    scope_key = models.CharField(max_length=64)
    collection = models.ForeignKey(MovieCollection, on_delete=models.PROTECT, related_name='draw_states')
    current_draw = models.ForeignKey('MovieDraw', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'collection'], name='movie_draw_state_scope_unique')]


class MovieDraw(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_key = models.CharField(max_length=64)
    collection = models.ForeignKey(MovieCollection, on_delete=models.PROTECT, related_name='draws')
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT, related_name='draws')
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='pending', choices=[(s, s) for s in ('pending', 'accepted', 'dismissed', 'invalidated')])
    eligible_count = models.PositiveIntegerField()
    candidate_snapshot = models.JSONField()
    selected_index = models.PositiveIntegerField()
    animation_duration_ms = models.PositiveIntegerField()
    selected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['scope_key', 'request_id'], name='movie_draw_request_unique'),
            models.UniqueConstraint(fields=['scope_key', 'collection'], condition=Q(status='pending'), name='movie_draw_pending_unique'),
            models.CheckConstraint(condition=Q(animation_duration_ms__gte=5000, animation_duration_ms__lte=10000), name='movie_draw_duration_range'),
            models.CheckConstraint(condition=Q(eligible_count__gte=1, selected_index__lt=models.F('eligible_count')), name='movie_draw_index_range'),
            models.CheckConstraint(condition=Q(status='pending', resolved_at__isnull=True) | Q(status__in=['accepted', 'dismissed', 'invalidated'], resolved_at__isnull=False), name='movie_draw_status_dates'),
        ]
        indexes = [models.Index(fields=['scope_key', 'collection', '-selected_at'], name='movie_draw_history_idx')]


class MovieProgressEvent(models.Model):
    scope_key = models.CharField(max_length=64)
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT, related_name='progress_events')
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    previous_status = models.CharField(max_length=12)
    next_status = models.CharField(max_length=12)
    occurred_at = models.DateTimeField(auto_now_add=True)
    draw = models.ForeignKey(MovieDraw, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'request_id'], name='movie_event_request_unique')]
        indexes = [models.Index(fields=['scope_key', '-occurred_at', '-id'], name='movie_event_history_idx')]


class MovieMutation(models.Model):
    """Resposta imutável para replay de qualquer mutação, incluindo dismiss."""
    scope_key = models.CharField(max_length=64)
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'request_id'], name='movie_mutation_request_unique')]


class MovieScopeLock(models.Model):
    """Mutex por identidade lógica para criação e idempotência entre coleções."""
    scope_key = models.CharField(max_length=64, unique=True)


class MovieTierState(models.Model):
    scope_key = models.CharField(max_length=64)
    collection = models.ForeignKey(MovieCollection, on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'collection'], name='movie_tier_state_scope_unique')]


class MovieTierRating(models.Model):
    scope_key = models.CharField(max_length=64)
    collection = models.ForeignKey(MovieCollection, on_delete=models.PROTECT)
    movie = models.ForeignKey(Movie, on_delete=models.PROTECT)
    tier = models.CharField(max_length=1, choices=[(tier, tier) for tier in 'SABCD'])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['scope_key', 'collection', 'movie'], name='movie_tier_rating_scope_unique'),
            models.CheckConstraint(condition=Q(tier__in=list('SABCD')), name='movie_tier_value_valid'),
        ]
