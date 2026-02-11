# Generated migration for DeletedTargetInfo

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedTargetInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_object_id', models.CharField(db_index=True, max_length=255)),
                ('document_id', models.CharField(blank=True, max_length=255, null=True)),
                ('label', models.CharField(blank=True, max_length=255, null=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('target_content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='contenttypes.contenttype')),
            ],
            options={
                'ordering': ('-created',),
                'app_label': 'events_document_id_fix',
            },
        ),
        migrations.AddConstraint(
            model_name='deletedtargetinfo',
            constraint=models.UniqueConstraint(fields=('target_content_type', 'target_object_id'), name='events_document_id_fix_deletedtargetinfo_unique'),
        ),
    ]
