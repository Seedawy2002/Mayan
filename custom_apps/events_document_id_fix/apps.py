from django.utils.translation import gettext_lazy as _
from mayan.apps.common.apps import MayanAppConfig

class EventsDocumentIdFixConfig(MayanAppConfig):
    has_rest_api = False
    has_tests = False
    name = 'events_document_id_fix'
    verbose_name = _('Events document ID fix')

    def ready(self):
        super().ready()
        self._set_document_id_resolver()
        self._patch_dynamic_serializer_field()
        self._patch_events_serializer()
        self._patch_events_views_renderer()
        self._patch_events_views_response()
        self._patch_json_renderer_global()
        self._patch_middleware()
        self._connect_save_target_before_delete()
        self._patch_trashed_document_task()

    def _set_document_id_resolver(self):
        """Register resolver so renderers/middleware can look up true document_id from DeletedTargetInfo."""
        try:
            from events_document_id_fix.renderers import set_get_document_id_resolver
            from events_document_id_fix.resolvers import get_document_id_for_event_item
            set_get_document_id_resolver(get_document_id_for_event_item)
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
            field_name = getattr(self, 'field_name', None) or getattr(self, '_field_name', None)
            if field_name == 'actor':
                obj_id = getattr(instance, 'actor_object_id', None)
                return {'id': int(obj_id) if obj_id is not None else None}
            if field_name == 'target':
                obj_id = getattr(instance, 'target_object_id', None)
                fix = {'id': int(obj_id) if obj_id is not None else None}
                target_model = getattr(getattr(instance, 'target_content_type', None), 'model', None)
                if target_model == 'document':
                    fix['document_id'] = fix['id']
                elif target_model == 'documenttype' and obj_id is not None:
                    doc_id = resolve_doc_id(instance, obj_id)
                    fix['document_id'] = int(doc_id) if doc_id is not None else None
                    fix['document_type_id'] = fix['id']
                return fix
            return result

        rest_api_fields.DynamicSerializerField.to_representation = patched_to_representation

    def _patch_events_serializer(self):
        """Patch base EventSerializer.target so deleted targets always return id (and saved metadata)."""
        try:
            from mayan.apps.events.serializers import event_serializers
        except ImportError:
            return
        from events_document_id_fix.serializers import EventTargetField

        EventSerializer = event_serializers.EventSerializer
        custom_target = EventTargetField(read_only=True)

        # DRF builds _declared_fields at class definition; assign both so instances use our field.
        EventSerializer.target = custom_target
        if hasattr(EventSerializer, '_declared_fields') and 'target' in EventSerializer._declared_fields:
            EventSerializer._declared_fields['target'] = custom_target

    def _patch_events_views_renderer(self):
        """Use our renderer on events API views so response is fixed before sending."""
        try:
            from mayan.apps.events.api_views import event_api_views as events_api_views
            from events_document_id_fix.renderers import EventsJSONRenderer
        except ImportError:
            return
        # Add our renderer first so it's used when rendering event list/detail.
        for name in dir(events_api_views):
            obj = getattr(events_api_views, name)
            if isinstance(obj, type) and hasattr(obj, 'renderer_classes'):
                classes = list(getattr(obj, 'renderer_classes', []))
                if EventsJSONRenderer not in classes:
                    obj.renderer_classes = (EventsJSONRenderer,) + tuple(classes)

    def _patch_events_views_response(self):
        """Wrap events view list/get so we fix response.data before return (guaranteed to run)."""
        try:
            from mayan.apps.events.api_views import event_api_views as events_api_views
            from events_document_id_fix.renderers import fix_events_target_in_data
        except ImportError:
            return

        def wrap_response_method(original):
            def wrapper(self, request, *args, **kwargs):
                response = original(self, request, *args, **kwargs)
                if getattr(response, 'data', None) is not None:
                    fix_events_target_in_data(response.data)
                return response
            return wrapper

        for name in dir(events_api_views):
            obj = getattr(events_api_views, name)
            if not isinstance(obj, type):
                continue
            if hasattr(obj, 'list'):
                obj.list = wrap_response_method(obj.list)
            if hasattr(obj, 'get'):
                obj.get = wrap_response_method(obj.get)
            if hasattr(obj, 'retrieve'):
                obj.retrieve = wrap_response_method(obj.retrieve)

    def _patch_json_renderer_global(self):
        """Fix events data before Response is rendered (last moment before bytes are sent)."""
        try:
            from rest_framework.response import Response
            from events_document_id_fix.renderers import fix_events_target_in_data
        except ImportError:
            return

        def _is_events_response(data):
            if not isinstance(data, dict):
                return False
            if 'results' in data:
                r = data.get('results')
                return isinstance(r, list) and (not r or (isinstance(r[0], dict) and 'verb' in r[0]))
            return 'target_object_id' in data and 'verb' in data

        # Patch Response.render() - runs right before content is generated
        _original_render = Response.render

        def _patched_render(self):
            if getattr(self, 'data', None) is not None and _is_events_response(self.data):
                fix_events_target_in_data(self.data)
            return _original_render(self)

        Response.render = _patched_render

        # Also patch JSONRenderer.render for non-Response render paths
        try:
            from rest_framework.renderers import JSONRenderer
            _orig_render = JSONRenderer.render

            def _patched_json_render(self, data, accepted_media_type=None, renderer_context=None):
                if _is_events_response(data):
                    fix_events_target_in_data(data)
                return _orig_render(self, data, accepted_media_type, renderer_context)

            JSONRenderer.render = _patched_json_render
        except ImportError:
            pass

    def _patch_middleware(self):
        """Ensure events API responses fix actor/target; insert at 0 so we run last on response."""
        from django.conf import settings
        middleware = getattr(settings, 'MIDDLEWARE', None) or getattr(settings, 'MIDDLEWARE_CLASSES', [])
        our_mw = 'events_document_id_fix.middleware.EventTargetResponseFixMiddleware'
        if our_mw in middleware:
            return
        mw_list = list(middleware)
        mw_list.insert(0, our_mw)
        setattr(settings, 'MIDDLEWARE', mw_list)

    def _connect_save_target_before_delete(self):
        """Save one row to TrashedDocumentDeletedInfo on document delete; link to event via post_save on Action."""
        import logging
        from django.db.models.signals import pre_delete, post_delete
        from django.utils import timezone

        logger = logging.getLogger('events_document_id_fix')

        try:
            from mayan.apps.documents.models import Document
        except ImportError:
            return

        def _emergency_log(msg):
            try:
                with open('/var/lib/mayan/emergency.log', 'a') as f:
                    f.write(f"[{timezone.now().isoformat()}] {msg}\n")
            except:
                pass

        def save_trashed_document_info(document_instance):
            """Create TrashedDocumentDeletedInfo row for this document (called from pre_delete and delete() patch)."""
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo

                doc_type_id = getattr(document_instance, 'document_type_id', None) or (
                    getattr(getattr(document_instance, 'document_type', None), 'pk', None)
                )
                if doc_type_id is None:
                    return
                doc_pk = getattr(document_instance, 'pk', None)
                if doc_pk is None:
                    return
                label = getattr(document_instance, 'label', None) or str(doc_pk)
                # get_or_create avoids duplicate when both pre_delete and delete() patch run
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

        def on_pre_delete(sender, instance, **kwargs):
            logger.debug('pre_delete fired: sender=%s instance.pk=%s', sender.__name__, getattr(instance, 'pk', None))
            save_trashed_document_info(instance)

        def on_post_delete(sender, instance, **kwargs):
            """Instance still has pk in memory after delete (Django keeps attributes)."""
            logger.debug('post_delete fired: sender=%s instance.pk=%s', sender.__name__, getattr(instance, 'pk', None))
            save_trashed_document_info(instance)

        pre_delete.connect(on_pre_delete, sender=Document, weak=False)
        post_delete.connect(on_post_delete, sender=Document, weak=False)

        # TrashedDocument is a proxy of Document - when users delete from trash, this is the model.
        # pre_delete sender is TrashedDocument, not Document, so we must connect to both.
        try:
            from mayan.apps.documents.models import TrashedDocument
            pre_delete.connect(on_pre_delete, sender=TrashedDocument, weak=False)
            post_delete.connect(on_post_delete, sender=TrashedDocument, weak=False)
            logger.info('Connected pre_delete and post_delete to Document and TrashedDocument')
            print("   -> Signals connected to TrashedDocument ✅", flush=True)
        except ImportError:
            logger.warning('TrashedDocument not found, only Document signals connected')
            print("   -> TrashedDocument model not found! ⚠️", flush=True)

        # Also patch Document.delete() so we capture when code calls document.delete()
        # (in case pre_delete doesn't run in some process, e.g. bulk path or worker)
        _original_delete = Document.delete

        def patched_delete(self, using=None, keep_parents=False):
            save_trashed_document_info(self)
            return _original_delete(self, using=using, keep_parents=keep_parents)

        Document.delete = patched_delete

        # TrashedDocument inherits delete() from Document - patching Document covers it
        # (proxy uses parent's delete method)

        # When actstream saves a trashed_document_deleted action, link our row to event_id
        self._connect_action_post_save()

        # Fallback: patch event_trashed_document_deleted.commit - captures document_id from caller
        # (Celery worker may load apps differently; pre_delete/delete patch can miss)
        self._patch_event_commit()

    def _patch_trashed_document_task(self):
        """Patch views that trigger delete - save document info before task is queued."""
        import logging
        from django.utils import timezone

        logger = logging.getLogger('events_document_id_fix')

        def save_doc_info(trashed_document):
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo
                doc_type_id = getattr(trashed_document, 'document_type_id', None) or (
                    getattr(getattr(trashed_document, 'document_type', None), 'pk', None)
                )
                if doc_type_id is None:
                    return
                doc_pk = getattr(trashed_document, 'pk', None)
                if doc_pk is None:
                    return
                label = getattr(trashed_document, 'label', None) or str(doc_pk)
                TrashedDocumentDeletedInfo.objects.get_or_create(
                    document_id=str(doc_pk),
                    defaults={
                        'document_type_id': int(doc_type_id),
                        'deleted_at': timezone.now(),
                        'label': (label[:255] if label else None),
                        'event_id': None,
                    }
                )
                logger.info('TrashedDocumentDeletedInfo SAVED: document_id=%s', doc_pk)
            except Exception as e:
                logger.exception('Save failed: %s', e)

        # Patch API view (DELETE /api/.../trashed-documents/{id}/)
        try:
            from mayan.apps.documents.api_views.trashed_document_api_views import APITrashedDocumentDetailView
            _orig_destroy = APITrashedDocumentDetailView.destroy

            def patched_destroy(self, request, *args, **kwargs):
                instance = self.get_object()
                save_doc_info(instance)
                return _orig_destroy(self, request, *args, **kwargs)

            APITrashedDocumentDetailView.destroy = patched_destroy
            logger.info('Patched APITrashedDocumentDetailView.destroy')
        except Exception as e:
            logger.warning('Could not patch API view: %s', e)

        # Patch web view (TrashedDocumentDeleteView)
        try:
            from mayan.apps.documents.views.trashed_document_views import TrashedDocumentDeleteView
            _orig_object_action = TrashedDocumentDeleteView.object_action

            def patched_object_action(self, form, instance):
                save_doc_info(instance)
                return _orig_object_action(self, form, instance)

            TrashedDocumentDeleteView.object_action = patched_object_action
            logger.info('Patched TrashedDocumentDeleteView.object_action')
        except Exception as e:
            logger.warning('Could not patch web view: %s', e)


    def _patch_event_commit(self):
        """Patch event_trashed_document_deleted.commit to save document_id from caller's self."""
        import inspect
        import logging
        from django.utils import timezone

        logger = logging.getLogger('events_document_id_fix')

        try:
            from mayan.apps.events.classes import EventType
            from mayan.apps.documents.events import event_trashed_document_deleted
        except ImportError:
            return

        _original_commit = EventType.commit

        def _capture_from_caller():
            """Get document_id from the code that called commit (Document.delete has self)."""
            frame = inspect.currentframe()
            if frame is None:
                return None
            try:
                # Call stack: our wrapper -> commit -> Document.delete
                for _ in range(8):
                    frame = frame.f_back
                    if frame is None:
                        return None
                    locals_dict = frame.f_locals
                    self_obj = locals_dict.get('self')
                    if self_obj is None:
                        continue
                    # Document or TrashedDocument
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

        def patched_commit(self, action_object=None, actor=None, target=None):
            if getattr(self, 'id', None) == 'documents.trashed_document_deleted':
                captured = _capture_from_caller()
                if captured:
                    doc_id, doc_type_id, label = captured
                    try:
                        from events_document_id_fix.models import TrashedDocumentDeletedInfo
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
            return _original_commit(self, action_object=action_object, actor=actor, target=target)

        EventType.commit = patched_commit
        logger.info('Patched EventType.commit for trashed_document_deleted')

    def _connect_action_post_save(self):
        """Set event_id on our TrashedDocumentDeletedInfo when the event (Action) is saved."""
        from django.apps import apps
        try:
            Action = apps.get_model('actstream', 'Action')
        except LookupError:
            return

        from django.db.models.signals import post_save

        def on_action_saved(sender, instance, created, **kwargs):
            if not created:
                return
            verb = getattr(instance, 'verb', None)
            if verb != 'documents.trashed_document_deleted':
                return
            target_ct_id = getattr(instance, 'target_content_type_id', None)
            target_obj_id = getattr(instance, 'target_object_id', None)
            if target_obj_id is None:
                return
            # DocumentType content type id (59 in your DB); we match by target_content_type if available
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo
                from django.contrib.contenttypes.models import ContentType
                doc_type_ct = ContentType.objects.get(app_label='documents', model='documenttype')
                if target_ct_id != doc_type_ct.pk:
                    return
            except Exception:
                return
            doc_type_id = int(target_obj_id)
            ts = getattr(instance, 'timestamp', None)
            event_id = getattr(instance, 'pk', None) or getattr(instance, 'id', None)
            if event_id is None:
                return
            # Find our most recent row for this document_type not yet linked, with deleted_at <= event time
            qs = TrashedDocumentDeletedInfo.objects.filter(
                document_type_id=doc_type_id,
                event_id__isnull=True,
            )
            if ts is not None:
                qs = qs.filter(deleted_at__lte=ts)
            row = qs.order_by('-deleted_at').first()
            if row:
                row.event_id = event_id
                row.save(update_fields=['event_id'])
            # signals end
        post_save.connect(on_action_saved, sender=Action, weak=False)
