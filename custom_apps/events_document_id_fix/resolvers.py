"""
Resolve true document_id for documents.trashed_document_deleted from
TrashedDocumentDeletedInfo (dedicated table for this event only).
"""


def get_document_id_for_event_item(event_item):
    """
    Return the true document_id for an event item (dict from API), or None.
    - When target is Document: return target_object_id.
    - When target is DocumentType: look up TrashedDocumentDeletedInfo by
      document_type_id + event timestamp.
    """
    if not isinstance(event_item, dict):
        return None
    target_model = _target_content_type_model(event_item)
    target_id = event_item.get('target_object_id')
    if target_id is None:
        return None
    # Target is Document -> target_object_id is the document pk.
    if target_model == 'document':
        return int(target_id)
    # Target is DocumentType -> use dedicated table (look up by event_id first, then timestamp).
    if target_model == 'documenttype':
        event_id = event_item.get('id')
        if event_id is not None:
            row = _trashed_by_event_id(event_id)
            if row is not None:
                return int(row.document_id)
        created_raw = event_item.get('created') or event_item.get('timestamp')
        if not created_raw:
            return _trashed_latest(int(target_id))
        try:
            event_created = _parse_created(created_raw)
        except (TypeError, ValueError):
            return _trashed_latest(int(target_id))
        doc_id = _trashed_at_time(int(target_id), event_created)
        return doc_id if doc_id is not None else _trashed_latest(int(target_id))
    return None


def _trashed_by_event_id(event_id):
    from events_document_id_fix.models import TrashedDocumentDeletedInfo
    return TrashedDocumentDeletedInfo.objects.filter(event_id=event_id).first()


def _target_content_type_model(item):
    ct = item.get('target_content_type') if isinstance(item, dict) else None
    if ct is None:
        return None
    return ct.get('model') if isinstance(ct, dict) else getattr(ct, 'model', None)


def _parse_created(value):
    if hasattr(value, 'timestamp'):
        return value
    if isinstance(value, str):
        from django.utils.dateparse import parse_datetime
        dt = parse_datetime(value)
        if dt is not None:
            from django.utils import timezone
            if timezone.is_naive(dt):
                return timezone.make_aware(dt)
            return dt
        from datetime import datetime
        s = value.replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    return value


def _trashed_latest(document_type_id):
    from events_document_id_fix.models import TrashedDocumentDeletedInfo
    row = TrashedDocumentDeletedInfo.objects.filter(
        document_type_id=document_type_id,
    ).order_by('-deleted_at').first()
    return int(row.document_id) if row else None


def _trashed_at_time(document_type_id, event_created):
    from events_document_id_fix.models import TrashedDocumentDeletedInfo
    row = TrashedDocumentDeletedInfo.objects.filter(
        document_type_id=document_type_id,
        deleted_at__lte=event_created,
    ).order_by('-deleted_at').first()
    return int(row.document_id) if row else None
