"""
Custom DRF renderer that fixes events API output: replace "target"/"actor" error strings
"Unable to find serializer..." with {"id": object_id, "document_id": ...} before the response is sent.
Uses TrashedDocumentDeletedInfo (via resolver) for the true document_id when event target is not Document.
"""
from rest_framework.renderers import JSONRenderer

UNABLE_PREFIX = 'Unable to find serializer'

# Set by apps.ready() when Django is available; used to resolve true document_id from DB.
_get_document_id_resolver = None


def set_get_document_id_resolver(fn):
    global _get_document_id_resolver
    _get_document_id_resolver = fn


def _verb_id(item):
    """Safely get verb id from event item (verb can be dict or object)."""
    verb = item.get('verb') if isinstance(item, dict) else None
    if verb is None:
        return None
    if isinstance(verb, dict):
        return verb.get('id')
    return getattr(verb, 'id', None)


def _target_content_type_model(item):
    """Safely get target_content_type.model (e.g. 'document' vs 'documenttype')."""
    ct = item.get('target_content_type') if isinstance(item, dict) else None
    if ct is None:
        return None
    if isinstance(ct, dict):
        return ct.get('model')
    return getattr(ct, 'model', None)


def _inject_document_id_for_trashed_deleted(item, fix):
    """Set fix['document_id'] for trashed_document_deleted (resolved value or null)."""
    if _verb_id(item) != 'documents.trashed_document_deleted':
        return
    doc_id = None
    if _get_document_id_resolver and callable(_get_document_id_resolver):
        try:
            doc_id = _get_document_id_resolver(item)
        except Exception:
            pass
    model = _target_content_type_model(item)
    if doc_id is None and model == 'document':
        doc_id = fix.get('id')
    fix['document_id'] = int(doc_id) if doc_id is not None else None
    if model == 'documenttype':
        fix['document_type_id'] = int(fix.get('id')) if fix.get('id') is not None else None


def _fix_event_item(item):
    """In-place fix actor and target for one event item. Only edits documents.trashed_document_deleted events."""
    if _verb_id(item) != 'documents.trashed_document_deleted':
        return False
    changed = False
    # Fix target
    target = item.get('target')
    if isinstance(target, str) and target.startswith(UNABLE_PREFIX):
        target_id = item.get('target_object_id')
        fix = {'id': int(target_id) if target_id is not None else None}
        _inject_document_id_for_trashed_deleted(item, fix)
        item['target'] = fix
        changed = True
    elif isinstance(target, dict):
        # Target is a dict. When target is DocumentType, document_id must come from
        # TrashedDocumentDeletedInfo; never use target_object_id (that's the doc type id).
        # If document_id equals id or is missing, we must overwrite for documenttype.
        target_model = _target_content_type_model(item)
        need_inject = (
            'document_id' not in target
            or (target_model == 'documenttype' and str(target.get('document_id', '')) == str(target.get('id', '')))
        )
        if need_inject:
            _inject_document_id_for_trashed_deleted(item, target)
            changed = True
    # Fix actor (only for trashed_document_deleted)
    actor = item.get('actor')
    if isinstance(actor, str) and actor.startswith(UNABLE_PREFIX):
        actor_id = item.get('actor_object_id')
        item['actor'] = {'id': int(actor_id) if actor_id is not None else None}
        changed = True
    return changed


def fix_events_target_in_data(data):
    """In-place fix: replace error string actor/target with id dict in event list/detail data. Never raises."""
    try:
        if not isinstance(data, dict):
            return False
        results = data.get('results')
        if results is None:
            # Single event (detail): data is the event dict
            return _fix_event_item(data)
        if not isinstance(results, list):
            return False
        # List: data has 'results'
        changed = False
        for item in results:
            if isinstance(item, dict) and _fix_event_item(item):
                changed = True
        return changed
    except Exception:
        return False


class EventsJSONRenderer(JSONRenderer):
    """JSON renderer that fixes event target fields before rendering."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        # Fix event target fields in the data dict before JSON encoding.
        fix_events_target_in_data(data)
        return super().render(data, accepted_media_type=accepted_media_type, renderer_context=renderer_context)
