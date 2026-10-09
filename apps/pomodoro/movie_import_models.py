import uuid

from django.conf import settings
from django.db import models


class MovieImportJob(models.Model):
    class Status(models.TextChoices):
        QUEUED_PREVIEW = 'queued_preview', 'Aguardando prévia'
        PREVIEWING = 'previewing', 'Preparando prévia'
        READY = 'ready', 'Prévia pronta — confirme para importar'
        QUEUED_IMPORT = 'queued_import', 'Aguardando importação'
        IMPORTING = 'importing', 'Importando'
        DONE = 'done', 'Importação concluída'
        FAILED = 'failed', 'Falha — nenhuma alteração deste processamento foi aplicada'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=255)
    csv_text = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED_PREVIEW, db_index=True)
    report = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
