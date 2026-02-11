"""
Store metadata for trashed_document_deleted events so the API can return
the true document_id when the event target is DocumentType.
"""
from django.db import models
from django.utils import timezone


class TrashedDocumentDeletedInfo(models.Model):
    """
    One row per document delete: links trashed_document_deleted event to document info.
    When listing events we look up by event_id (or document_type_id + timestamp) to get document_id/label.
    """
    document_id = models.CharField(max_length=255, db_index=True)
    document_type_id = models.PositiveIntegerField(db_index=True)
    deleted_at = models.DateTimeField(default=timezone.now, db_index=True)
    label = models.CharField(max_length=255, null=True, blank=True)
    # Link to actstream_action.id so we can look up by event when serializing
    event_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        app_label = 'events_document_id_fix'
        ordering = ('-deleted_at',)

    def __str__(self):
        return f"doc {self.document_id} (type {self.document_type_id})"
