"""
Custom event serializers so that trashed_document_deleted (and other events
with missing/unserializable target) return document/target id (and saved metadata)
instead of "Unable to find serializer class for: ...".
"""
from mayan.apps.rest_api.fields import DynamicSerializerField


def _get_document_type_label(doc_type_id):
    """Look up DocumentType label by id. Returns str or None."""
    if doc_type_id is None:
        return None
    try:
        from mayan.apps.documents.models import DocumentType
        label = DocumentType.objects.filter(pk=int(doc_type_id)).values_list('label', flat=True).first()
        return label or None
    except Exception:
        return None


def _get_document_type_id_from_document(doc_id):
    """Look up document_type_id from Document or TrashedDocument by document id. Returns int or None."""
    if doc_id is None:
        return None
    try:
        from mayan.apps.documents.models import Document
        doc_type_id = Document.objects.filter(pk=doc_id).values_list('document_type_id', flat=True).first()
        if doc_type_id is not None:
            return int(doc_type_id)
        try:
            from mayan.apps.documents.models import TrashedDocument
            doc_type_id = TrashedDocument.objects.filter(pk=doc_id).values_list('document_type_id', flat=True).first()
            return int(doc_type_id) if doc_type_id is not None else None
        except Exception:
            pass
        return None
    except Exception:
        return None

def _cabinet_stub_for_action_field(action, field_name):
    """Return stub dict for actor/target/action_object when it's DeletedCabinetStub."""
    try:
        from events_document_id_fix.models import DeletedCabinetStub
        from django.contrib.contenttypes.models import ContentType
        if field_name == 'actor':
            ct_id = getattr(action, 'actor_content_type_id', None)
            obj_id = getattr(action, 'actor_object_id', None)
        elif field_name == 'action_object':
            ct_id = getattr(action, 'action_object_content_type_id', None)
            obj_id = getattr(action, 'action_object_object_id', None)
        else:
            ct_id = getattr(action, 'target_content_type_id', None)
            obj_id = getattr(action, 'target_object_id', None)
        if ct_id is None or obj_id is None or obj_id == '':
            return None
        ct = ContentType.objects.get_for_id(ct_id)
        app_label = getattr(ct, 'app_label', None)
        model = getattr(ct, 'model', None)
        stub = None
        if app_label == 'events_document_id_fix' and model == 'deletedcabinetstub':
            stub_pk = int(obj_id) if isinstance(obj_id, str) else obj_id
            stub = DeletedCabinetStub.objects.filter(pk=stub_pk).first()
        elif app_label == 'cabinets' and model == 'cabinet':
            cabinet_id = int(obj_id) if isinstance(obj_id, str) else obj_id
            stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_id).order_by('-deleted_at').first()
        if stub is None:
            return None
        return {
            'id': stub.cabinet_id,
            'stub_id': stub.pk,
            'cabinet_id': stub.cabinet_id,
            'label': stub.label,
            'parent_id': stub.parent_id,
            'full_path': stub.full_path,
            'children': [],
        }
    except Exception:
        return None


class CabinetStubField(DynamicSerializerField):
    """Field that returns DeletedCabinetStub dict for cabinet_deleted events (actor/target/action_object)."""

    def __init__(self, stub_field_name='actor', **kwargs):
        self._stub_field_name = stub_field_name
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        """Return stub dict when actor/target/action_object is DeletedCabinetStub (avoids 'Unable to find serializer')."""
        stub = _cabinet_stub_for_action_field(instance, self._stub_field_name)
        if stub is not None:
            return stub
        return super().get_attribute(instance)

    def to_representation(self, value):
        """If value is already our stub dict, return it; otherwise delegate."""
        if isinstance(value, dict) and 'id' in value and 'label' in value and 'parent_id' in value:
            return value
        return super().to_representation(value)


class EventTargetField(DynamicSerializerField):
    """
    Target field that, for deleted or missing targets (e.g. trashed_document_deleted),
    returns {"id": target_object_id, "document_id": ..., "label": ...} using the event's
    stored target_object_id and any metadata we saved before delete.
    """

    def get_attribute(self, instance):
        """Return stub dict when target is DeletedCabinetStub (avoids 'Unable to find serializer')."""
        stub = _cabinet_stub_for_action_field(instance, 'target')
        if stub is not None:
            return stub
        return super().get_attribute(instance)

    def to_representation(self, value):
        """If value is already our stub dict, return it; otherwise delegate."""
        if isinstance(value, dict) and 'id' in value and 'label' in value and 'parent_id' in value:
            return value
        return super().to_representation(value)

    def _cabinet_stub_result(self, action):
        """Build stub dict for deleted cabinet from DeletedCabinetStub (target_object_id is cabinet_id or stub pk)."""
        target_id = getattr(action, 'target_object_id', None) if action else None
        if target_id is None:
            return {'id': None, 'cabinet_id': None, 'label': None, 'parent_id': None, 'full_path': None, 'children': []}
        try:
            from events_document_id_fix.models import DeletedCabinetStub
            target_app = getattr(getattr(action, 'target_content_type', None), 'app_label', None)
            target_model = getattr(getattr(action, 'target_content_type', None), 'model', None)
            stub = None
            if target_app == 'events_document_id_fix' and target_model == 'deletedcabinetstub':
                stub_pk = int(target_id) if isinstance(target_id, str) else target_id
                stub = DeletedCabinetStub.objects.filter(pk=stub_pk).first()
            else:
                cabinet_id = int(target_id) if isinstance(target_id, str) else target_id
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_id).order_by('-deleted_at').first()
            if stub is None:
                cab_id = int(target_id) if isinstance(target_id, str) else target_id
                return {'id': cab_id, 'cabinet_id': cab_id, 'label': None, 'parent_id': None, 'full_path': None, 'children': []}
            return {
                'id': stub.cabinet_id,
                'stub_id': stub.pk,
                'cabinet_id': stub.cabinet_id,
                'label': stub.label,
                'parent_id': stub.parent_id,
                'full_path': stub.full_path,
                'children': [],
            }
        except Exception:
            cab_id = int(target_id) if isinstance(target_id, str) else target_id
            return {'id': cab_id, 'cabinet_id': cab_id, 'label': None, 'parent_id': None, 'full_path': None, 'children': []}

    def _target_id_result(self, action):
        """Build target dict from event and TrashedDocumentDeletedInfo (true document_id)."""
        target_id = getattr(action, 'target_object_id', None) if action else None
        result = {'id': int(target_id) if target_id is not None else None}
        event_created = (getattr(action, 'timestamp', None) or getattr(action, 'created', None)) if action else None
        target_model = None
        target_app = None
        if action and getattr(action, 'target_content_type', None):
            target_model = getattr(action.target_content_type, 'model', None)
            target_app = getattr(action.target_content_type, 'app_label', None)

        # Target is deleted cabinet (Cabinet or repointed DeletedCabinetStub) -> use DeletedCabinetStub.
        if (target_app == 'cabinets' and target_model == 'cabinet') or (target_app == 'events_document_id_fix' and target_model == 'deletedcabinetstub'):
            return self._cabinet_stub_result(action)

        # Target is Document -> target_object_id is document_id.
        if target_model == 'document':
            result['document_id'] = result['id']
            # Add document_type for extraction: document_data.get('document_type', {}).get('label')
            doc_type_id = _get_document_type_id_from_document(result['id'])
            doc_type_label = _get_document_type_label(doc_type_id) if doc_type_id else None
            if doc_type_id is not None and doc_type_label is not None:
                result['document_type'] = {'id': doc_type_id, 'label': doc_type_label}
            return result
        # Target is DocumentType -> look up TrashedDocumentDeletedInfo (linked by event_id or timestamp).
        if target_model == 'documenttype' and target_id is not None:
            # Add document_type for extraction: document_data.get('document_type', {}).get('label')
            doc_type_label = _get_document_type_label(target_id)
            if doc_type_label is not None:
                result['document_type'] = {'id': int(target_id), 'label': doc_type_label}
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
        # If value is already our cabinet stub dict (from get_attribute), return it.
        if isinstance(value, dict) and 'id' in value and 'label' in value and 'parent_id' in value:
            return value
        action = self.parent.instance
        verb_id = self._verb_id(action)

        # For trashed_document_deleted and cabinet_deleted, use stub/target result when target is missing.
        if verb_id in ('documents.trashed_document_deleted', 'cabinets.cabinet_deleted'):
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
