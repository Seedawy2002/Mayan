"""
Migration: add DeletedCabinetStub table to store cabinet metadata
before deletion so we can reconstruct event targets/action_objects
for cabinet_deleted / cabinet_created events after the cabinet is gone.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events_document_id_fix', '0005_document_id_unique'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedCabinetStub',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cabinet_id', models.PositiveIntegerField(db_index=True, unique=True)),
                ('label', models.CharField(blank=True, max_length=255, null=True)),
                ('parent_id', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('full_path', models.CharField(blank=True, max_length=1024, null=True)),
                ('deleted_at', models.DateTimeField(db_index=True)),
            ],
            options={
                'app_label': 'events_document_id_fix',
                'ordering': ('-deleted_at',),
            },
        ),
    ]
