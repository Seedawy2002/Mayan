# Dedicated table for documents.trashed_document_deleted only; replace DeletedTargetInfo.

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ('events_document_id_fix', '0002_remove_unique_for_document_type_lookup'),
    ]

    operations = [
        migrations.CreateModel(
            name='TrashedDocumentDeletedInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_id', models.CharField(db_index=True, max_length=255)),
                ('document_type_id', models.PositiveIntegerField(db_index=True)),
                ('deleted_at', models.DateTimeField(db_index=True, default=timezone.now)),
                ('label', models.CharField(blank=True, max_length=255, null=True)),
            ],
            options={
                'ordering': ('-deleted_at',),
                'app_label': 'events_document_id_fix',
            },
        ),
        migrations.DeleteModel(
            name='DeletedTargetInfo',
        ),
    ]
