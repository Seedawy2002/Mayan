# Allow multiple DeletedTargetInfo rows per (content_type, object_id) so we can
# store (DocumentType, type_id) -> document_id for each deleted document and look
# up by event timestamp.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('events_document_id_fix', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='deletedtargetinfo',
            name='events_document_id_fix_deletedtargetinfo_unique',
        ),
    ]
