# Add unique constraint on document_id for get_or_create (avoids duplicate rows)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mayan_event_enrichment', '0004_trasheddocumentdeletedinfo_event_id'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='trasheddocumentdeletedinfo',
            constraint=models.UniqueConstraint(
                fields=('document_id',),
                name='mayan_event_enrichment_document_id_unique'
            ),
        ),
    ]
