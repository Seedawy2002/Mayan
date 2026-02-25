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


class DeletedCabinetStub(models.Model):
    """
    Store cabinet metadata before deletion so event API can serialize
    actor/action_object/target for cabinet_deleted events after the cabinet is gone.
    """
    cabinet_id = models.PositiveIntegerField(db_index=True, unique=True)
    label = models.CharField(max_length=255, null=True, blank=True)
    parent_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    full_path = models.CharField(max_length=1024, null=True, blank=True)
    deleted_at = models.DateTimeField(db_index=True, default=timezone.now)

    class Meta:
        app_label = 'events_document_id_fix'
        ordering = ('-deleted_at',)

    def __str__(self):
        return f"cabinet {self.cabinet_id} ({self.full_path or self.label})"


class DeletedCabinetEvent(models.Model):
    """
    Copy of a cabinet_deleted Action row so the event survives when Action is
    cascade-deleted. Actor/target/action_object reference DeletedCabinetStub (stub pk).
    action_id = original Action.pk to avoid duplicate rows when same action refs multiple cabinets.
    """
    action_id = models.PositiveIntegerField(null=True, blank=True, db_index=True, unique=True)
    verb = models.CharField(max_length=255, db_index=True)
    timestamp = models.DateTimeField(db_index=True, default=timezone.now)
    actor_content_type_id = models.PositiveIntegerField()
    actor_object_id = models.CharField(max_length=255)
    target_content_type_id = models.PositiveIntegerField()
    target_object_id = models.CharField(max_length=255)
    action_object_content_type_id = models.PositiveIntegerField(null=True, blank=True)
    action_object_object_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = 'events_document_id_fix'
        ordering = ('-timestamp',)

    def __str__(self):
        return f"cabinet_deleted @ {self.timestamp}"
