import logging
from django.utils.translation import gettext_lazy as _
from mayan.apps.common.apps import MayanAppConfig

logger = logging.getLogger(__name__)

class EventsDocumentIdFixConfig(MayanAppConfig):
    has_rest_api = False
    has_tests = False
    name = 'events_document_id_fix'
    verbose_name = _('Events document ID fix')

    def ready(self):
        super().ready()
        for fn in (
            self._ensure_cabinet_deleted_event_type,
            self._patch_dynamic_serializer_field,
            self._patch_events_serializer,
            self._patch_events_list_queryset,
            self._connect_save_target_before_delete,
            self._connect_cabinet_pre_delete,
            self._patch_cabinet_delete,
            self._patch_trashed_document_task,
        ):
            try:
                fn()
            except Exception:
                logger.exception('events_document_id_fix: %s failed', getattr(fn, '__name__', str(fn)))

    def _ensure_cabinet_deleted_event_type(self):
        """Register cabinets.cabinet_deleted at load time so EventTypeSerializer can resolve it."""
        try:
            from mayan.apps.events.classes import EventType
            EventType.get('cabinets.cabinet_deleted')
        except (KeyError, ImportError):
            try:
                from mayan.apps.cabinets.events import namespace as cabinets_namespace
                from django.utils.translation import gettext_lazy as _
                cabinets_namespace.add_event_type(
                    name='cabinet_deleted', label=_('Cabinet deleted')
                )
            except Exception:
                pass

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

        def _get_doc_type_label(doc_type_id):
            if doc_type_id is None:
                return None
            try:
                from mayan.apps.documents.models import DocumentType
                return DocumentType.objects.filter(pk=int(doc_type_id)).values_list('label', flat=True).first() or None
            except Exception:
                return None

        def _cabinet_stub_for_field(instance, field_name):
            """Return stub dict for deleted cabinet from DeletedCabinetStub (cabinet or stub content type)."""
            try:
                from events_document_id_fix.models import DeletedCabinetStub
                from django.contrib.contenttypes.models import ContentType
                ct_id = None
                obj_id = None
                if field_name == 'actor':
                    ct_id = getattr(instance, 'actor_content_type_id', None)
                    obj_id = getattr(instance, 'actor_object_id', None)
                elif field_name == 'action_object':
                    ct_id = getattr(instance, 'action_object_content_type_id', None)
                    obj_id = getattr(instance, 'action_object_object_id', None)
                elif field_name == 'target':
                    ct_id = getattr(instance, 'target_content_type_id', None)
                    obj_id = getattr(instance, 'target_object_id', None)
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

        def patched_to_representation(self, value):
            parent = getattr(self, 'parent', None)
            instance = getattr(parent, 'instance', None) if parent else None
            field_name = getattr(self, 'field_name', None)
            verb = getattr(instance, 'verb', None) if instance else None
            verb_id = verb if isinstance(verb, str) else getattr(verb, 'id', None)
            if verb_id == 'cabinets.cabinet_deleted' and instance and field_name in ('actor', 'action_object', 'target'):
                cabinet_stub = _cabinet_stub_for_field(instance, field_name)
                if cabinet_stub is not None:
                    return cabinet_stub
            result = original_to_representation(self, value)
            if not isinstance(result, str) or not result.startswith('Unable to find serializer'):
                return result
            if instance is None:
                return result
            cabinet_stub = _cabinet_stub_for_field(instance, field_name)
            if cabinet_stub is not None:
                return cabinet_stub
            verb = getattr(instance, 'verb', None)
            verb_id = verb if isinstance(verb, str) else getattr(verb, 'id', None)
            if verb_id != 'documents.trashed_document_deleted':
                return result
            if field_name == 'target':
                obj_id = getattr(instance, 'target_object_id', None)
                fix = {'id': int(obj_id) if obj_id is not None else None}
                target_model = getattr(getattr(instance, 'target_content_type', None), 'model', None)
                if target_model == 'document':
                    fix['document_id'] = fix['id']
                    doc_type_label = None
                    if fix.get('id'):
                        try:
                            from mayan.apps.documents.models import Document
                            dt_id = Document.objects.filter(pk=fix['id']).values_list('document_type_id', flat=True).first()
                            if dt_id is None:
                                from mayan.apps.documents.models import TrashedDocument
                                dt_id = TrashedDocument.objects.filter(pk=fix['id']).values_list('document_type_id', flat=True).first()
                            doc_type_label = _get_doc_type_label(dt_id)
                        except Exception:
                            pass
                    if doc_type_label is not None:
                        fix['document_type'] = {'id': int(dt_id), 'label': doc_type_label}
                elif target_model == 'documenttype' and obj_id is not None:
                    doc_id = resolve_doc_id(instance, obj_id)
                    fix['id'] = int(doc_id) if doc_id is not None else fix['id']
                    fix['document_id'] = int(doc_id) if doc_id is not None else None
                    doc_type_label = _get_doc_type_label(obj_id)
                    if doc_type_label is not None:
                        fix['document_type'] = {'id': int(obj_id), 'label': doc_type_label}
                return fix
            return result

        rest_api_fields.DynamicSerializerField.to_representation = patched_to_representation

    def _patch_events_serializer(self):
        """Patch base EventSerializer.target so deleted targets always return id.
        Also add action_object and action_object_content_type so cabinet events
        (e.g. cabinets.add_document) include the cabinet id in the response."""
        try:
            from mayan.apps.events.serializers import event_serializers
        except ImportError:
            return
        from mayan.apps.common.serializers import ContentTypeSerializer
        from mayan.apps.rest_api.fields import DynamicSerializerField
        from events_document_id_fix.serializers import EventTargetField, CabinetStubField

        EventSerializer = event_serializers.EventSerializer
        custom_target = EventTargetField(read_only=True)
        custom_actor = CabinetStubField(stub_field_name='actor', read_only=True)
        custom_action_object = CabinetStubField(stub_field_name='action_object', read_only=True)

        EventSerializer.target = custom_target
        EventSerializer.actor = custom_actor
        EventSerializer.action_object = custom_action_object
        if hasattr(EventSerializer, '_declared_fields'):
            if 'target' in EventSerializer._declared_fields:
                EventSerializer._declared_fields['target'] = custom_target
            if 'actor' in EventSerializer._declared_fields:
                EventSerializer._declared_fields['actor'] = custom_actor
            # Ensure `action_object` is actually included as a serializer field.
            # Some Mayan versions don't declare it by default.
            EventSerializer._declared_fields['action_object'] = custom_action_object

        # Add action_object_content_type (cabinet for cabinets.add_document)
        EventSerializer.action_object_content_type = ContentTypeSerializer(read_only=True)
        if hasattr(EventSerializer, '_declared_fields'):
            EventSerializer._declared_fields['action_object_content_type'] = EventSerializer.action_object_content_type
        # Remove action_object and action_object_content_type from exclude so they appear in the response
        if hasattr(EventSerializer.Meta, 'exclude') and EventSerializer.Meta.exclude:
            EventSerializer.Meta.exclude = tuple(
                f for f in EventSerializer.Meta.exclude
                if f not in ('action_object', 'action_object_content_type')
            )

        # Normalize deleted cabinet stub IDs in API output:
        # - actor/target/action_object dict `id` is the original cabinet_id
        # - include `stub_id` for debugging
        # - *_object_id fields in the event payload are rewritten to cabinet_id too
        try:
            _orig_to_rep = EventSerializer.to_representation
        except Exception:
            _orig_to_rep = None

        if _orig_to_rep is not None:
            def _rewrite_stub_object_id(instance, ct, obj_id):
                try:
                    if not ct or obj_id in (None, ''):
                        return None
                    if getattr(ct, 'app_label', None) != 'events_document_id_fix' or getattr(ct, 'model', None) != 'deletedcabinetstub':
                        return None
                    from events_document_id_fix.models import DeletedCabinetStub
                    stub = DeletedCabinetStub.objects.filter(pk=int(obj_id)).first()
                    return str(stub.cabinet_id) if stub else None
                except Exception:
                    return None

            def patched_to_representation(self_ser, instance):
                data = _orig_to_rep(self_ser, instance)
                try:
                    # actor_object_id
                    actor_ct = getattr(instance, 'actor_content_type', None)
                    actor_obj_id = getattr(instance, 'actor_object_id', None)
                    new_actor_obj_id = _rewrite_stub_object_id(instance, actor_ct, actor_obj_id)
                    if new_actor_obj_id is not None:
                        data['actor_object_id'] = new_actor_obj_id

                    # target_object_id
                    target_ct = getattr(instance, 'target_content_type', None)
                    target_obj_id = getattr(instance, 'target_object_id', None)
                    new_target_obj_id = _rewrite_stub_object_id(instance, target_ct, target_obj_id)
                    if new_target_obj_id is not None:
                        data['target_object_id'] = new_target_obj_id

                    # action_object_object_id
                    ao_ct = getattr(instance, 'action_object_content_type', None)
                    ao_obj_id = getattr(instance, 'action_object_object_id', None)
                    new_ao_obj_id = _rewrite_stub_object_id(instance, ao_ct, ao_obj_id)
                    if new_ao_obj_id is not None:
                        data['action_object_object_id'] = new_ao_obj_id
                except Exception:
                    return data
                return data

            EventSerializer.to_representation = patched_to_representation

    def _patch_events_list_queryset(self):
        """Include cabinet_deleted events in the events list; patch both get_queryset and filter so ACL doesn't drop them."""
        try:
            # Mayan moved the events API views into a submodule; keep both imports
            # for compatibility across versions.
            try:
                from mayan.apps.events.api_views.event_api_views import APIEventListView
            except Exception:
                from mayan.apps.events.api_views import APIEventListView
            from actstream.models import Action
        except ImportError:
            return

        def _cabinet_deleted_actions_queryset():
            from events_document_id_fix.models import DeletedCabinetStub
            from django.contrib.contenttypes.models import ContentType
            from django.db.models import Q
            cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
            stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
            cabinet_ids = list(DeletedCabinetStub.objects.values_list('cabinet_id', flat=True))
            cabinet_id_strs = [str(i) for i in cabinet_ids]
            q_cabinet = Q(pk=-1)
            if cabinet_ct and cabinet_id_strs:
                q_cabinet = (
                    Q(actor_content_type_id=cabinet_ct.id, actor_object_id__in=cabinet_id_strs) |
                    Q(target_content_type_id=cabinet_ct.id, target_object_id__in=cabinet_id_strs) |
                    Q(action_object_content_type_id=cabinet_ct.id, action_object_object_id__in=cabinet_id_strs)
                )
            q_stub = (
                Q(actor_content_type_id=stub_ct.id) |
                Q(target_content_type_id=stub_ct.id) |
                Q(action_object_content_type_id=stub_ct.id)
            )
            return Action.objects.filter(verb='cabinets.cabinet_deleted').filter(q_cabinet | q_stub)

        # NOTE: Mayan's REST API base classes disallow overriding `get_queryset`
        # (they require `get_source_queryset` instead). Keep our changes limited
        # to `list()` so we don't trigger ImproperlyConfigured errors.

        # Merge DeletedCabinetEvent into the list (events copied before cascade-delete)
        def _wrap_deleted_event(ev):
            """Make DeletedCabinetEvent look like Action for EventSerializer."""
            from django.contrib.contenttypes.models import ContentType

            def _resolve_obj(ct_id, obj_id):
                if not ct_id or obj_id is None or obj_id == '':
                    return None
                try:
                    ct = ContentType.objects.get_for_id(ct_id)
                    return ct.get_object_for_this_type(pk=obj_id)
                except Exception:
                    return None

            class Wrapper:
                pass
            w = Wrapper()
            w.pk = w.id = ev.pk
            w.verb = ev.verb
            w.timestamp = ev.timestamp
            w.actor_content_type_id = ev.actor_content_type_id
            w.actor_object_id = ev.actor_object_id
            w.target_content_type_id = ev.target_content_type_id
            w.target_object_id = ev.target_object_id
            w.action_object_content_type_id = ev.action_object_content_type_id
            w.action_object_object_id = ev.action_object_object_id
            w.actor_content_type = ContentType.objects.get_for_id(ev.actor_content_type_id) if ev.actor_content_type_id else None
            w.target_content_type = ContentType.objects.get_for_id(ev.target_content_type_id) if ev.target_content_type_id else None
            w.action_object_content_type = ContentType.objects.get_for_id(ev.action_object_content_type_id) if ev.action_object_content_type_id else None
            # Resolve actor/target/action_object so EventSerializer can access them (e.g. instance.actor)
            w.actor = _resolve_obj(ev.actor_content_type_id, ev.actor_object_id)
            w.target = _resolve_obj(ev.target_content_type_id, ev.target_object_id)
            w.action_object = _resolve_obj(ev.action_object_content_type_id, ev.action_object_object_id)
            return w

        # Patch list() to merge DeletedCabinetEvent into results (they survive cascade-delete)
        _original_list = APIEventListView.list
        def _is_misleading_parent_event(action):
            """Filter out Mayan's cabinet_deleted where actor=parent cabinet, target=null (child was deleted, not parent)."""
            try:
                if getattr(action, 'verb', None) != 'cabinets.cabinet_deleted':
                    return False
                actor_ct_id = getattr(action, 'actor_content_type_id', None)
                target_ct_id = getattr(action, 'target_content_type_id', None)
                target_obj_id = getattr(action, 'target_object_id', None)
                target_obj_id_str = str(target_obj_id).strip().lower() if target_obj_id is not None else ''
                target_is_missing = (target_ct_id is None) and (target_obj_id is None or target_obj_id_str in ('', 'none', 'null'))
                if not target_is_missing:
                    return False
                # In our setup, a legitimate cabinet_deleted should always have a target.
                # Mayan sometimes emits a cabinet_deleted with target=NULL (typically parent as actor),
                # which is misleading and should always be dropped, even if the cabinet is already deleted.
                return True
            except Exception:
                pass
            return False

        def patched_list(self_view, request, *args, **kwargs):
            from rest_framework.response import Response
            from events_document_id_fix.models import DeletedCabinetEvent
            try:
                queryset = self_view.filter_queryset(self_view.get_queryset())
                # Preserve Mayan's normal ordering (including `?_ordering=` via MayanSortingFilter).
                all_actions = list(queryset)
                all_actions = [a for a in all_actions if not _is_misleading_parent_event(a)]
                action_pks = {getattr(a, 'pk', None) or getattr(a, 'id', None) for a in all_actions} - {None}
                deleted = list(DeletedCabinetEvent.objects.all())
                # Exclude DeletedCabinetEvent rows whose action_id is already in all_actions (avoid duplicates)
                deleted = [ev for ev in deleted if ev.action_id not in action_pks]
                if deleted:
                    wraps = [_wrap_deleted_event(ev) for ev in deleted]
                    combined = all_actions + wraps

                    # Respect the same ordering requested by the API client.
                    try:
                        ordering_spec = request.query_params.get('_ordering')
                    except Exception:
                        ordering_spec = None
                    tokens = [t.strip() for t in (ordering_spec or '').split(',') if t.strip()]
                    if not tokens:
                        tokens = ['-timestamp']

                    def _token_key(obj, token):
                        desc = token.startswith('-')
                        field = token[1:] if desc else token
                        value = getattr(obj, field, None)
                        if field in ('id', 'pk') and value is None:
                            value = getattr(obj, 'pk', None) or getattr(obj, 'id', None)
                        if field == 'timestamp' and value is not None:
                            try:
                                value = float(value.timestamp())
                            except Exception:
                                value = None
                        if value is None:
                            return (1, 0)
                        if isinstance(value, (int, float)):
                            return (0, -value if desc else value)
                        # Fallback for non-numeric sorts: best effort (descending not guaranteed).
                        return (0, value)

                    combined.sort(
                        key=lambda o: tuple(_token_key(o, t) for t in tokens)
                    )
                else:
                    combined = all_actions
                page = self_view.paginate_queryset(combined)
                if page is not None:
                    serializer = self_view.get_serializer(page, many=True)
                    return self_view.get_paginated_response(serializer.data)
                serializer = self_view.get_serializer(combined, many=True)
                return Response(serializer.data)
            except Exception as e:
                logger.exception(
                    'events_document_id_fix: patched_list failed, falling back to original: %s', e
                )
                return _original_list(self_view, request, *args, **kwargs)
        APIEventListView.list = patched_list

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

    def _connect_cabinet_pre_delete(self):
        """Emit cabinet_deleted for every cabinet (including parents) and save DeletedCabinetStub."""
        from django.db.models.signals import pre_delete
        from django.utils import timezone
        try:
            from mayan.apps.cabinets.models import Cabinet
        except ImportError:
            return
        try:
            from mayan.apps.events.classes import EventType
            event_cabinet_deleted = EventType.get('cabinets.cabinet_deleted')
        except (KeyError, ImportError):
            try:
                from mayan.apps.cabinets.events import namespace as cabinets_namespace
                from django.utils.translation import gettext_lazy as _
                event_cabinet_deleted = cabinets_namespace.add_event_type(
                    name='cabinet_deleted', label=_('Cabinet deleted')
                )
            except Exception:
                return

        def on_cabinet_pre_delete(sender, instance, **kwargs):
            def _compute_full_path(cabinet):
                """
                Build full path from cabinet parent chain.
                We avoid relying on Cabinet.get_full_path() because in some Mayan
                builds it returns only the cabinet label (not the full chain).
                """
                try:
                    parts = []
                    seen = set()
                    current = cabinet
                    depth = 0
                    while current is not None and depth < 64:
                        pk = getattr(current, 'pk', None)
                        if pk is not None:
                            if pk in seen:
                                break
                            seen.add(pk)
                        label = getattr(current, 'label', None)
                        label = (str(label).strip() if label is not None else '')
                        parts.append(label or (str(pk) if pk is not None else ''))
                        parent = getattr(current, 'parent', None)
                        if parent is None:
                            parent_id = getattr(current, 'parent_id', None)
                            if parent_id:
                                try:
                                    from mayan.apps.cabinets.models import Cabinet
                                    parent = Cabinet.objects.filter(pk=parent_id).first()
                                except Exception:
                                    parent = None
                        current = parent
                        depth += 1
                    parts = [p for p in reversed(parts) if p]
                    return ' / '.join(parts) if parts else None
                except Exception:
                    return None

            try:
                from events_document_id_fix.models import DeletedCabinetStub
                full_path = _compute_full_path(instance) or getattr(instance, 'get_full_path', lambda: None)()
                DeletedCabinetStub.objects.update_or_create(
                    cabinet_id=instance.pk,
                    defaults={
                        'label': (instance.label[:255] if instance.label else None),
                        'parent_id': getattr(instance, 'parent_id', None),
                        'full_path': (full_path[:1024] if full_path else None),
                        'deleted_at': timezone.now(),
                    }
                )
            except Exception:
                pass
            # Copy cabinet_deleted actions to our table first (in case another handler cascade-deletes them)
            _copy_cabinet_actions_to_deleted_event(instance)
            # Repoint any cabinet_deleted actions referencing this cabinet so they aren't cascade-deleted
            _repoint_actions_for_cabinet(instance)
            # Create Action for both parent and child cabinets so cabinet_id is always in the response
            cabinet_id = instance.pk
            from django.db import transaction
            transaction.on_commit(lambda: _create_cabinet_deleted_action_directly(cabinet_id))

        def _create_cabinet_deleted_action_directly(cabinet_id):
            """Create actstream Action row - references DeletedCabinetStub so it survives cabinet delete."""
            try:
                from actstream.models import Action
                from events_document_id_fix.models import DeletedCabinetStub
                from django.contrib.contenttypes.models import ContentType
                cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_id).order_by('-deleted_at').first()
                if not cabinet_ct or not stub:
                    logger.warning('events_document_id_fix: no stub for cabinet_id=%s', cabinet_id)
                    return
                stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
                stub_id_str = str(stub.pk)
                parent_id = stub.parent_id
                if parent_id is not None:
                    parent_stub = DeletedCabinetStub.objects.filter(cabinet_id=parent_id).order_by('-deleted_at').first()
                    if parent_stub is not None:
                        action_obj_ct_id = stub_ct.id
                        action_obj_id = str(parent_stub.pk)
                    else:
                        action_obj_ct_id = cabinet_ct.id
                        action_obj_id = str(parent_id)
                else:
                    action_obj_ct_id = stub_ct.id
                    action_obj_id = stub_id_str
                Action.objects.create(
                    verb='cabinets.cabinet_deleted',
                    timestamp=timezone.now(),
                    actor_content_type_id=stub_ct.id,
                    actor_object_id=stub_id_str,
                    target_content_type_id=stub_ct.id,
                    target_object_id=stub_id_str,
                    action_object_content_type_id=action_obj_ct_id,
                    action_object_object_id=action_obj_id or '',
                    public=True,
                )
                logger.info('events_document_id_fix: cabinet_deleted Action created for cabinet_id=%s', cabinet_id)
            except Exception as e:
                logger.exception(
                    'events_document_id_fix: failed to create cabinet_deleted Action for cabinet_id=%s: %s',
                    cabinet_id, e,
                )

        def _copy_single_action_to_deleted_event(action, cabinet_instance):
            """Copy one cabinet_deleted action to DeletedCabinetEvent (for the one we just created)."""
            try:
                from events_document_id_fix.models import DeletedCabinetStub, DeletedCabinetEvent
                from django.contrib.contenttypes.models import ContentType
                cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_instance.pk).order_by('-deleted_at').first()
                if not cabinet_ct or not stub:
                    return
                stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
                cab_id_str = str(cabinet_instance.pk)
                stub_id_str = str(stub.pk)
                ac = stub_ct.id if getattr(action, 'actor_content_type_id', None) == cabinet_ct.id and getattr(action, 'actor_object_id', None) == cab_id_str else getattr(action, 'actor_content_type_id', None)
                ao = stub_id_str if ac == stub_ct.id else getattr(action, 'actor_object_id', None)
                tc = stub_ct.id if getattr(action, 'target_content_type_id', None) == cabinet_ct.id and getattr(action, 'target_object_id', None) == cab_id_str else getattr(action, 'target_content_type_id', None)
                to = stub_id_str if tc == stub_ct.id else getattr(action, 'target_object_id', None)
                aoc, aoo = getattr(action, 'action_object_content_type_id', None), getattr(action, 'action_object_object_id', None)
                if aoc == cabinet_ct.id and str(aoo) == cab_id_str:
                    aoc, aoo = stub_ct.id, stub_id_str
                action_pk = getattr(action, 'pk', None) or getattr(action, 'id', None)
                DeletedCabinetEvent.objects.get_or_create(
                    action_id=action_pk,
                    defaults={
                        'verb': action.verb,
                        'timestamp': getattr(action, 'timestamp', timezone.now()),
                        'actor_content_type_id': ac,
                        'actor_object_id': ao or '',
                        'target_content_type_id': tc,
                        'target_object_id': to or '',
                        'action_object_content_type_id': aoc,
                        'action_object_object_id': aoo or '',
                    }
                )
            except Exception:
                pass

        def _copy_cabinet_actions_to_deleted_event(cabinet_instance):
            """Copy cabinet_deleted actions that reference this cabinet to DeletedCabinetEvent (so they survive cascade delete)."""
            try:
                from actstream.models import Action
                from events_document_id_fix.models import DeletedCabinetStub, DeletedCabinetEvent
                from django.contrib.contenttypes.models import ContentType
                cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
                if not cabinet_ct:
                    return
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_instance.pk).order_by('-deleted_at').first()
                if not stub:
                    return
                stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
                cab_id_str = str(cabinet_instance.pk)
                stub_id_str = str(stub.pk)
                from django.db.models import Q
                actions = Action.objects.filter(verb='cabinets.cabinet_deleted').filter(
                    Q(actor_content_type_id=cabinet_ct.id, actor_object_id=cab_id_str) |
                    Q(target_content_type_id=cabinet_ct.id, target_object_id=cab_id_str) |
                    Q(action_object_content_type_id=cabinet_ct.id, action_object_object_id=cab_id_str)
                )
                for action in actions:
                    ac = stub_ct.id if getattr(action, 'actor_content_type_id', None) == cabinet_ct.id and getattr(action, 'actor_object_id', None) == cab_id_str else getattr(action, 'actor_content_type_id', None)
                    ao = stub_id_str if ac == stub_ct.id else getattr(action, 'actor_object_id', None)
                    tc = stub_ct.id if getattr(action, 'target_content_type_id', None) == cabinet_ct.id and getattr(action, 'target_object_id', None) == cab_id_str else getattr(action, 'target_content_type_id', None)
                    to = stub_id_str if tc == stub_ct.id else getattr(action, 'target_object_id', None)
                    aoc = getattr(action, 'action_object_content_type_id', None)
                    aoo = getattr(action, 'action_object_object_id', None)
                    if aoc == cabinet_ct.id and str(aoo) == cab_id_str:
                        aoc, aoo = stub_ct.id, stub_id_str
                    action_pk = getattr(action, 'pk', None) or getattr(action, 'id', None)
                    defaults = {
                        'verb': action.verb,
                        'timestamp': getattr(action, 'timestamp', timezone.now()),
                        'actor_content_type_id': ac,
                        'actor_object_id': ao or '',
                        'target_content_type_id': tc,
                        'target_object_id': to or '',
                        'action_object_content_type_id': aoc,
                        'action_object_object_id': aoo or '',
                    }
                    if action_pk is not None:
                        DeletedCabinetEvent.objects.get_or_create(action_id=action_pk, defaults=defaults)
                    else:
                        DeletedCabinetEvent.objects.create(**defaults)
            except Exception:
                pass

        def _repoint_actions_for_cabinet(cabinet_instance):
            """Repoint all cabinet_deleted actions that reference this cabinet to DeletedCabinetStub."""
            try:
                from actstream.models import Action
                from events_document_id_fix.models import DeletedCabinetStub
                from django.contrib.contenttypes.models import ContentType
                cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
                if not cabinet_ct:
                    return
                cab_id_str = str(cabinet_instance.pk)
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_instance.pk).order_by('-deleted_at').first()
                if not stub:
                    return
                stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
                stub_id_str = str(stub.pk)
                from django.db.models import Q
                actions = Action.objects.filter(verb='cabinets.cabinet_deleted').filter(
                    Q(actor_content_type_id=cabinet_ct.id, actor_object_id=cab_id_str) |
                    Q(target_content_type_id=cabinet_ct.id, target_object_id=cab_id_str) |
                    Q(action_object_content_type_id=cabinet_ct.id, action_object_object_id=cab_id_str)
                )
                for action in actions:
                    _repoint_action_to_stub(action, cabinet_instance)
            except Exception:
                pass

        def _repoint_action_to_stub(action, cabinet_instance):
            """Set actor/target/action_object to DeletedCabinetStub so Action isn't cascade-deleted."""
            try:
                from events_document_id_fix.models import DeletedCabinetStub
                from django.contrib.contenttypes.models import ContentType
                stub = DeletedCabinetStub.objects.filter(cabinet_id=cabinet_instance.pk).order_by('-deleted_at').first()
                if not stub:
                    return
                stub_ct = ContentType.objects.get_for_model(DeletedCabinetStub)
                stub_id_str = str(stub.pk)
                cabinet_ct = ContentType.objects.filter(app_label='cabinets', model='cabinet').first()
                if not cabinet_ct:
                    return
                cab_id_str = str(cabinet_instance.pk)
                updated = False
                if getattr(action, 'actor_content_type_id', None) == cabinet_ct.id and getattr(action, 'actor_object_id', None) == cab_id_str:
                    action.actor_content_type = stub_ct
                    action.actor_object_id = stub_id_str
                    updated = True
                if getattr(action, 'target_content_type_id', None) == cabinet_ct.id and getattr(action, 'target_object_id', None) == cab_id_str:
                    action.target_content_type = stub_ct
                    action.target_object_id = stub_id_str
                    updated = True
                if getattr(action, 'action_object_content_type_id', None) == cabinet_ct.id and getattr(action, 'action_object_object_id', None) == cab_id_str:
                    action.action_object_content_type = stub_ct
                    action.action_object_object_id = stub_id_str
                    updated = True
                if updated:
                    action.save()
            except Exception:
                pass

        pre_delete.connect(on_cabinet_pre_delete, sender=Cabinet, weak=False)

    def _patch_cabinet_delete(self):
        """Patch Cabinet.delete() so children are deleted via ORM first; each then fires pre_delete and events."""
        try:
            from mayan.apps.cabinets.models import Cabinet
        except ImportError:
            return
        _original_delete = Cabinet.delete

        def patched_delete(self_cabinet, *args, **kwargs):
            # Delete children first so each triggers pre_delete and cabinet_deleted events
            children_rel = getattr(self_cabinet, 'children', None) or getattr(self_cabinet, 'cabinet_set', None)
            if children_rel is not None:
                children = list(children_rel.all())
            else:
                children = list(Cabinet.objects.filter(parent_id=getattr(self_cabinet, 'pk', None)))
            for child in children:
                child.delete(*args, **kwargs)
            return _original_delete(self_cabinet, *args, **kwargs)

        Cabinet.delete = patched_delete

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
