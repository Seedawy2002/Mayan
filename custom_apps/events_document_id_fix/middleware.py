"""
Middleware that fixes events API responses: replace "actor"/"target" error strings
with {"id": object_id, "document_id": ...} so API consumers always get usable data.

Also: save document info when DELETE trashed_documents/{id}/ BEFORE the view runs.
"""
import json
import re
from django.utils import timezone

from events_document_id_fix.renderers import fix_events_target_in_data


def _save_trashed_document_before_delete(request):
    """If DELETE or POST to trashed_documents/{id}/, save doc info before view deletes it."""
    method = getattr(request, 'method', '') or request.META.get('REQUEST_METHOD', '')
    if method not in ('DELETE', 'POST'):
        return
    path = getattr(request, 'path', '') or getattr(request, 'path_info', '') or ''
    # Match API: /api/v4/trashed-documents/123/ or Web: /documents/trashed_documents/123/delete/
    m = re.search(r'trashed[-_]documents/(\d+)', path)
    if not m:
        return
    doc_id = m.group(1)
    import logging
    logging.getLogger('events_document_id_fix').info('Middleware: saving doc_id=%s path=%s', doc_id, path[:80])
    try:
        from django.apps import apps
        from events_document_id_fix.models import TrashedDocumentDeletedInfo
        TrashedDocument = apps.get_model('documents', 'TrashedDocument')
        doc = TrashedDocument.objects.filter(pk=int(doc_id)).first()
        if doc:
            doc_type_id = getattr(doc, 'document_type_id', None) or getattr(getattr(doc, 'document_type', None), 'pk', None)
            if doc_type_id:
                label = getattr(doc, 'label', None) or str(doc_id)
                TrashedDocumentDeletedInfo.objects.get_or_create(
                    document_id=str(doc_id),
                    defaults={
                        'document_type_id': int(doc_type_id),
                        'deleted_at': timezone.now(),
                        'label': (label[:255] if label else None),
                        'event_id': None,
                    }
                )
    except Exception:
        pass


def _looks_like_events_json(data):
    """True if data looks like events list or single event (so we should try to fix)."""
    if not isinstance(data, dict):
        return False
    if 'results' in data:
        results = data.get('results')
        return isinstance(results, list) and (
            not results or (isinstance(results[0], dict) and 'verb' in results[0])
        )
    return 'verb' in data and ('target' in data or 'target_object_id' in data)


class EventTargetResponseFixMiddleware:
    """
    When the events API returns actor/target as "Unable to find serializer...",
    replace with {"id": actor_object_id} / {"id": target_object_id, "document_id": ...}.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _save_trashed_document_before_delete(request)
        response = self.get_response(request)
        path = (getattr(request, 'path', '') or getattr(request, 'path_info', '') or '')
        if response.status_code != 200:
            return response
        # Only touch events API
        if '/api/' not in path or 'events' not in path:
            return response

        response['X-Events-Fix-Middleware'] = 'ran'

        # 1) Fix .data in place (used when DRF renders after we return)
        if getattr(response, 'data', None) is not None:
            fix_events_target_in_data(response.data)

        # 2) Fix .content if present (response may already be rendered; rewrite body)
        try:
            content = getattr(response, 'content', None)
            if not content:
                return response
            if isinstance(content, bytes):
                content = content.decode(getattr(response, 'charset', None) or 'utf-8')
            content_stripped = content.strip()
            if not content_stripped.startswith('{'):
                return response
            data = json.loads(content)
            if not _looks_like_events_json(data):
                return response
            if fix_events_target_in_data(data):
                new_body = json.dumps(data, ensure_ascii=False)
                response.content = new_body.encode(response.charset or 'utf-8')
                if 'Content-Length' in response:
                    response['Content-Length'] = str(len(response.content))
                response['X-Events-Fix'] = 'applied'
        except (ValueError, AttributeError, TypeError, KeyError):
            pass
        return response
