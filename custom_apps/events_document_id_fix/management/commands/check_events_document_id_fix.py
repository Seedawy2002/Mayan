"""
Management command to verify events_document_id_fix app and show TrashedDocumentDeletedInfo table.

Run: docker exec -u mayan mayan-app /opt/mayan-edms/bin/mayan-edms.py check_events_document_id_fix
     docker exec -u mayan mayan-app /opt/mayan-edms/bin/mayan-edms.py check_events_document_id_fix --test-insert 123 1

Or use psql: docker exec mayan-postgresql psql -U postgres -d mayan -c "SELECT * FROM events_document_id_fix_trasheddocumentdeletedinfo ORDER BY deleted_at DESC LIMIT 5;"
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Show TrashedDocumentDeletedInfo table status'

    def add_arguments(self, parser):
        parser.add_argument('--test-insert', nargs='+', metavar=('DOC_ID', 'TYPE_ID'), help='Insert: doc_id [type_id]. Use --event-id for event_id')
        parser.add_argument('--event-id', type=int, help='Set event_id (use with --test-insert)')

    def handle(self, *args, **options):
        if options.get('test_insert'):
            doc_id = options['test_insert'][0]
            type_id = options['test_insert'][1] if len(options['test_insert']) > 1 else 1
            event_id = options.get('event_id')
            try:
                from events_document_id_fix.models import TrashedDocumentDeletedInfo
                obj, created = TrashedDocumentDeletedInfo.objects.update_or_create(
                    document_id=str(doc_id),
                    defaults={
                        'document_type_id': int(type_id),
                        'deleted_at': timezone.now(),
                        'label': f'Test doc {doc_id}',
                        'event_id': event_id,
                    }
                )
                self.stdout.write(self.style.SUCCESS(f'Inserted: document_id={doc_id} event_id={event_id}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Failed: {e}'))
            return

        self.stdout.write('events_document_id_fix check:\n')

        from events_document_id_fix.models import TrashedDocumentDeletedInfo
        count = TrashedDocumentDeletedInfo.objects.count()
        self.stdout.write(f'  TrashedDocumentDeletedInfo rows: {count}')

        if count > 0:
            for row in TrashedDocumentDeletedInfo.objects.order_by('-deleted_at')[:5]:
                self.stdout.write(f'    id={row.id} document_id={row.document_id} event_id={row.event_id}')
        else:
            self.stdout.write(self.style.WARNING(
                '\n  Table is empty. To test: run with --test-insert 999 1'
            ))
