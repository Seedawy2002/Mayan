"""
Custom event serializers so that trashed_document_deleted (and other events
with missing/unserializable target) return document/target id (and saved metadata)
instead of "Unable to find serializer class for: ...".
"""
from mayan.apps.rest_api.fields import DynamicSerializerField


class EventTargetField(DynamicSerializerField):
    """
    Target field that, for deleted or missing targets (e.g. trashed_document_deleted),
    returns {"id": target_object_id, "document_id": ..., "label": ...} using the event's
    stored target_object_id and any metadata we saved before delete.
    """

    def _target_id_result(self, action):
        """Build target dict from event and TrashedDocumentDeletedInfo (true document_id)."""
        target_id = getattr(action, 'target_object_id', None) if action else None
        result = {'id': int(target_id) if target_id is not None else None}
        event_created = (getattr(action, 'timestamp', None) or getattr(action, 'created', None)) if action else None
        target_model = None
        if action and getattr(action, 'target_content_type', None):
            target_model = getattr(action.target_content_type, 'model', None)

        # Target is Document -> target_object_id is document_id.
        if target_model == 'document':
            result['document_id'] = result['id']
            return result
        # Target is DocumentType -> look up TrashedDocumentDeletedInfo (linked by event_id or timestamp).
        if target_model == 'documenttype' and target_id is not None:
            result['document_type_id'] = int(target_id)
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo
                event_id = getattr(action, 'pk', None) or getattr(action, 'id', None)
                row = None
                if event_id is not None:
                    row = TrashedDocumentDeletedInfo.objects.filter(event_id=event_id).first()
                if row is None:
                    doc_type_id = int(target_id)
                    qs = TrashedDocumentDeletedInfo.objects.filter(document_type_id=doc_type_id)
                    if event_created is not None:
                        qs = qs.filter(deleted_at__lte=event_created)
                    row = qs.order_by('-deleted_at').first()
                    if row is None:
                        row = TrashedDocumentDeletedInfo.objects.filter(
                            document_type_id=doc_type_id,
                        ).order_by('-deleted_at').first()
                if row:
                    result['id'] = int(row.document_id)
                    result['document_id'] = int(row.document_id)
                    if row.label:
                        result['label'] = row.label
                else:
                    result['document_id'] = None
            except (ValueError, TypeError, Exception):
                result['document_id'] = None
        return result

    def _verb_id(self, action):
        """Verb can be a string or an object with .id (e.g. StoredEventType)."""
        verb = getattr(action, 'verb', None) if action else None
        if verb is None:
            return None
        if isinstance(verb, str):
            return verb
        return getattr(verb, 'id', None)

    def to_representation(self, value):
        action = self.parent.instance
        verb_id = self._verb_id(action)

        # Only edit trashed_document_deleted events; leave all others to base behavior.
        if verb_id == 'documents.trashed_document_deleted':
            if value is None:
                return self._target_id_result(action)
            if isinstance(value, str) and value.startswith('Unable to find serializer'):
                return self._target_id_result(action)
            try:
                result = super().to_representation(value)
                if isinstance(result, str) and result.startswith('Unable to find serializer'):
                    return self._target_id_result(action)
                return result
            except Exception:
                return self._target_id_result(action)

        return super().to_representation(value)
