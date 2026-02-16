from django.utils.translation import gettext_lazy as _
from mayan.apps.common.apps import MayanAppConfig

class EventsDocumentIdFixConfig(MayanAppConfig):
    has_rest_api = False
    has_tests = False
    name = 'events_document_id_fix'
    verbose_name = _('Events document ID fix')

    def ready(self):
        super().ready()
        self._patch_dynamic_serializer_field()
        self._patch_events_serializer()
        self._connect_save_target_before_delete()
        self._patch_trashed_document_task()

    def capture_deletion_metadata(self, document_instance):
        """Create TrashedDocumentDeletedInfo row for a document before it's deleted."""
        try:
            from events_document_id_fix.models import TrashedDocumentDeletedInfo
            from django.utils import timezone

            doc_type_id = getattr(document_instance, 'document_type_id', None) or (
                getattr(getattr(document_instance, 'document_type', None), 'pk', None)
            )
            doc_pk = getattr(document_instance, 'pk', None)

            if doc_type_id is not None and doc_pk is not None:
                label = getattr(document_instance, 'label', None) or str(doc_pk)
                TrashedDocumentDeletedInfo.objects.get_or_create(
                    document_id=str(doc_pk),
                    defaults={
                        'document_type_id': int(doc_type_id),
                        'deleted_at': timezone.now(),
                        'label': (label[:255] if label else None),
                        'event_id': None,
                    }
                )
        except Exception:
            pass

    def _resolve_doc_id_for_documenttype_target(self, action, target_obj_id):
        """Look up document_id from TrashedDocumentDeletedInfo for documenttype target."""
        try:
            from events_document_id_fix.models import TrashedDocumentDeletedInfo
            event_id = getattr(action, 'pk', None) or getattr(action, 'id', None)
            event_created = getattr(action, 'timestamp', None) or getattr(action, 'created', None)
            doc_type_id = int(target_obj_id)
            row = None
            if event_id is not None:
                row = TrashedDocumentDeletedInfo.objects.filter(event_id=event_id).first()
            if row is None:
                qs = TrashedDocumentDeletedInfo.objects.filter(document_type_id=doc_type_id)
                if event_created is not None:
                    qs = qs.filter(deleted_at__lte=event_created)
                row = qs.order_by('-deleted_at').first()
            if row is None:
                row = TrashedDocumentDeletedInfo.objects.filter(
                    document_type_id=doc_type_id,
                ).order_by('-deleted_at').first()
            return str(row.document_id) if row else None
        except (ValueError, TypeError, Exception):
            return None

    def _patch_dynamic_serializer_field(self):
        """Patch DynamicSerializerField so 'Unable to find serializer' becomes id dict for actor/target."""
        try:
            from mayan.apps.rest_api import fields as rest_api_fields
        except ImportError:
            return
        original_to_representation = rest_api_fields.DynamicSerializerField.to_representation
        resolve_doc_id = self._resolve_doc_id_for_documenttype_target

        def patched_to_representation(self, value):
            result = original_to_representation(self, value)
            if not isinstance(result, str) or not result.startswith('Unable to find serializer'):
                return result
            parent = getattr(self, 'parent', None)
            if parent is None:
                return result
            instance = getattr(parent, 'instance', None)
            if instance is None:
                return result
            verb = getattr(instance, 'verb', None)
            verb_id = verb if isinstance(verb, str) else getattr(verb, 'id', None)
            if verb_id != 'documents.trashed_document_deleted':
                return result  # Only edit trashed_document_deleted events
            if field_name == 'target':
                obj_id = getattr(instance, 'target_object_id', None)
                fix = {'id': int(obj_id) if obj_id is not None else None}
                target_model = getattr(getattr(instance, 'target_content_type', None), 'model', None)
                if target_model == 'document':
                    fix['document_id'] = fix['id']
                elif target_model == 'documenttype' and obj_id is not None:
                    doc_id = resolve_doc_id(instance, obj_id)
                    fix['id'] = int(doc_id) if doc_id is not None else fix['id']
                    fix['document_id'] = int(doc_id) if doc_id is not None else None
                    fix['document_type_id'] = int(obj_id) if obj_id is not None else None
                return fix
            return result

        rest_api_fields.DynamicSerializerField.to_representation = patched_to_representation

    def _patch_events_serializer(self):
        """Patch base EventSerializer.target so deleted targets always return id."""
        try:
            from mayan.apps.events.serializers import event_serializers
        except ImportError:
            return
        from events_document_id_fix.serializers import EventTargetField

        EventSerializer = event_serializers.EventSerializer
        custom_target = EventTargetField(read_only=True)

        EventSerializer.target = custom_target
        if hasattr(EventSerializer, '_declared_fields') and 'target' in EventSerializer._declared_fields:
            EventSerializer._declared_fields['target'] = custom_target

    def _connect_save_target_before_delete(self):
        """Connect signals to capture document metadata when a document record is about to be deleted."""
        from django.db.models.signals import pre_delete, post_delete
        try:
            from mayan.apps.documents.models import Document
        except ImportError:
            return

        def on_delete(sender, instance, **kwargs):
            self.capture_deletion_metadata(instance)

        pre_delete.connect(on_delete, sender=Document, weak=False)
        post_delete.connect(on_delete, sender=Document, weak=False)

        try:
            from mayan.apps.documents.models import TrashedDocument
            pre_delete.connect(on_delete, sender=TrashedDocument, weak=False)
            post_delete.connect(on_delete, sender=TrashedDocument, weak=False)
        except ImportError:
            pass

        # Patch Document.delete() for direct calls
        _original_delete = Document.delete
        def patched_delete(self_doc, using=None, keep_parents=False):
            self.capture_deletion_metadata(self_doc)
            return _original_delete(self_doc, using=using, keep_parents=keep_parents)
        Document.delete = patched_delete

        self._connect_action_post_save()
        self._patch_event_commit()

    def _patch_trashed_document_task(self):
        """Patch views that trigger background deletion tasks."""
        try:
            from mayan.apps.documents.api_views.trashed_document_api_views import APITrashedDocumentDetailView
            _orig_destroy = APITrashedDocumentDetailView.destroy

            def patched_destroy(self_view, request, *args, **kwargs):
                instance = self_view.get_object()
                self.capture_deletion_metadata(instance)
                return _orig_destroy(self_view, request, *args, **kwargs)

            APITrashedDocumentDetailView.destroy = patched_destroy
        except (ImportError, Exception):
            pass

        try:
            from mayan.apps.documents.views.trashed_document_views import TrashedDocumentDeleteView
            _orig_object_action = TrashedDocumentDeleteView.object_action

            def patched_object_action(self_view, form, instance):
                self.capture_deletion_metadata(instance)
                return _orig_object_action(self_view, form, instance)

            TrashedDocumentDeleteView.object_action = patched_object_action
        except (ImportError, Exception):
            pass

    def _patch_event_commit(self):
        """Patch event_trashed_document_deleted.commit to capture document_id from caller's stack."""
        import inspect
        try:
            from mayan.apps.events.classes import EventType
        except ImportError:
            return

        _original_commit = EventType.commit

        def _capture_from_caller():
            frame = inspect.currentframe()
            if frame is None:
                return None
            try:
                for _ in range(8):
                    frame = frame.f_back
                    if frame is None:
                        return None
                    locals_dict = frame.f_locals
                    self_obj = locals_dict.get('self')
                    if self_obj is None:
                        continue
                    cls = getattr(self_obj, '__class__', None)
                    if cls is None:
                        continue
                    mod = getattr(cls, '__module__', '') or ''
                    name = getattr(cls, '__name__', '') or ''
                    if 'document' in mod.lower() and name in ('Document', 'TrashedDocument'):
                        pk = getattr(self_obj, 'pk', None)
                        doc_type_id = getattr(self_obj, 'document_type_id', None) or (
                            getattr(getattr(self_obj, 'document_type', None), 'pk', None)
                        )
                        if pk is not None and doc_type_id is not None:
                            return (str(pk), int(doc_type_id), getattr(self_obj, 'label', None) or str(pk))
            finally:
                del frame
            return None

        def patched_commit(self_event, action_object=None, actor=None, target=None):
            if getattr(self_event, 'id', None) == 'documents.trashed_document_deleted':
                captured = _capture_from_caller()
                if captured:
                    doc_id, doc_type_id, label = captured
                    try:
                        from events_document_id_fix.models import TrashedDocumentDeletedInfo
                        from django.utils import timezone
                        TrashedDocumentDeletedInfo.objects.get_or_create(
                            document_id=doc_id,
                            defaults={
                                'document_type_id': doc_type_id,
                                'deleted_at': timezone.now(),
                                'label': (label[:255] if label else None),
                                'event_id': None,
                            }
                        )
                    except Exception:
                        pass
            return _original_commit(self_event, action_object=action_object, actor=actor, target=target)

        EventType.commit = patched_commit

    def _connect_action_post_save(self):
        """Link event_id to our TrashedDocumentDeletedInfo metadata when the Action is created."""
        from django.apps import apps
        try:
            Action = apps.get_model('actstream', 'Action')
        except LookupError:
            return
        from django.db.models.signals import post_save

        def on_action_saved(sender, instance, created, **kwargs):
            if not created or getattr(instance, 'verb', None) != 'documents.trashed_document_deleted':
                return
            target_obj_id = getattr(instance, 'target_object_id', None)
            if target_obj_id is None:
                return
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo
                from django.contrib.contenttypes.models import ContentType
                doc_type_ct = ContentType.objects.get(app_label='documents', model='documenttype')
                if getattr(instance, 'target_content_type_id', None) != doc_type_ct.pk:
                    return
                doc_type_id = int(target_obj_id)
                ts = getattr(instance, 'timestamp', None)
                event_id = getattr(instance, 'pk', None) or getattr(instance, 'id', None)
                qs = TrashedDocumentDeletedInfo.objects.filter(document_type_id=doc_type_id, event_id__isnull=True)
                if ts is not None:
                    qs = qs.filter(deleted_at__lte=ts)
                row = qs.order_by('-deleted_at').first()
                if row:
                    row.event_id = event_id
                    row.save(update_fields=['event_id'])
            except Exception:
                pass
        post_save.connect(on_action_saved, sender=Action, weak=False)
