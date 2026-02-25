# Generated migration: add DeletedCabinetEvent to store cabinet_deleted events when Action is cascade-deleted

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('events_document_id_fix', '0007_remove_trasheddocumentdeletedinfo_events_document_id_fix_document_id_unique_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedCabinetEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action_id', models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True)),
                ('verb', models.CharField(db_index=True, max_length=255)),
                ('timestamp', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('actor_content_type_id', models.PositiveIntegerField()),
                ('actor_object_id', models.CharField(max_length=255)),
                ('target_content_type_id', models.PositiveIntegerField()),
                ('target_object_id', models.CharField(max_length=255)),
                ('action_object_content_type_id', models.PositiveIntegerField(blank=True, null=True)),
                ('action_object_object_id', models.CharField(blank=True, max_length=255, null=True)),
            ],
            options={
                'app_label': 'events_document_id_fix',
                'ordering': ('-timestamp',),
            },
        ),
    ]
