# Add event_id to link deleted-doc row to actstream_action.id

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events_document_id_fix', '0003_trashed_document_deleted_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='trasheddocumentdeletedinfo',
            name='event_id',
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
