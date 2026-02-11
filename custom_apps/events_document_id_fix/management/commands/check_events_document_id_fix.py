"""
Management command to verify events_document_id_fix app and show TrashedDocumentDeletedInfo table.

Run: docker exec -u mayan mayan-app /opt/mayan-edms/bin/mayan-edms.py check_events_document_id_fix

Or use psql: docker exec mayan-postgresql psql -U postgres -d mayan -c "SELECT * FROM events_document_id_fix_trasheddocumentdeletedinfo ORDER BY deleted_at DESC LIMIT 5;"
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Show TrashedDocumentDeletedInfo table status'

    def handle(self, *args, **options):
        self.stdout.write('events_document_id_fix check:\n')

        from events_document_id_fix.models import TrashedDocumentDeletedInfo
        count = TrashedDocumentDeletedInfo.objects.count()
        self.stdout.write(f'  TrashedDocumentDeletedInfo rows: {count}')

        if count > 0:
            for row in TrashedDocumentDeletedInfo.objects.order_by('-deleted_at')[:5]:
                self.stdout.write(f'    id={row.id} document_id={row.document_id} event_id={row.event_id}')
        else:
            self.stdout.write(self.style.WARNING(
                '\n  Table is empty. Restart gunicorn AND Celery worker, then delete a trashed document.'
            ))
