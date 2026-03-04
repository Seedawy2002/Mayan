"""
Migration: rename app from 'events_document_id_fix' to 'mayan_event_enrichment'.

This migration:
1. Renames DB tables (events_document_id_fix_* -> mayan_event_enrichment_*)
2. Updates django_content_type rows so that GenericForeignKeys still resolve correctly.
3. Updates django_migrations rows (the old app_label entries become mayan_event_enrichment).

Uses SeparateDatabaseAndState so Django's ORM state is updated without re-running
the original table-creation operations.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mayan_event_enrichment', '0008_deletedcabinetevent'),
    ]

    operations = [
        # --- Rename physical DB tables (idempotent: only rename if old name still exists) ---
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'events_document_id_fix_trasheddocumentdeletedinfo') THEN
                        ALTER TABLE events_document_id_fix_trasheddocumentdeletedinfo
                            RENAME TO mayan_event_enrichment_trasheddocumentdeletedinfo;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'events_document_id_fix_deletedcabinetstub') THEN
                        ALTER TABLE events_document_id_fix_deletedcabinetstub
                            RENAME TO mayan_event_enrichment_deletedcabinetstub;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'events_document_id_fix_deletedcabinetevent') THEN
                        ALTER TABLE events_document_id_fix_deletedcabinetevent
                            RENAME TO mayan_event_enrichment_deletedcabinetevent;
                    END IF;
                END $$;
            """,
            reverse_sql="""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mayan_event_enrichment_trasheddocumentdeletedinfo') THEN
                        ALTER TABLE mayan_event_enrichment_trasheddocumentdeletedinfo
                            RENAME TO events_document_id_fix_trasheddocumentdeletedinfo;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mayan_event_enrichment_deletedcabinetstub') THEN
                        ALTER TABLE mayan_event_enrichment_deletedcabinetstub
                            RENAME TO events_document_id_fix_deletedcabinetstub;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mayan_event_enrichment_deletedcabinetevent') THEN
                        ALTER TABLE mayan_event_enrichment_deletedcabinetevent
                            RENAME TO events_document_id_fix_deletedcabinetevent;
                    END IF;
                END $$;
            """,
        ),

        # --- Update django_content_type so GenericForeignKeys resolve to the renamed app ---
        migrations.RunSQL(
            sql="""
                UPDATE django_content_type
                SET app_label = 'mayan_event_enrichment'
                WHERE app_label = 'events_document_id_fix';
            """,
            reverse_sql="""
                UPDATE django_content_type
                SET app_label = 'events_document_id_fix'
                WHERE app_label = 'mayan_event_enrichment';
            """,
        ),

        # --- Update django_migrations so future migrate runs don't re-run old migrations ---
        migrations.RunSQL(
            sql="""
                UPDATE django_migrations
                SET app = 'mayan_event_enrichment'
                WHERE app = 'events_document_id_fix';
            """,
            reverse_sql="""
                UPDATE django_migrations
                SET app = 'events_document_id_fix'
                WHERE app = 'mayan_event_enrichment';
            """,
        ),

    ]
