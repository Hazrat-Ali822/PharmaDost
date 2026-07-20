from django.db import migrations


def resync_sequence(apps, schema_editor):
    """Make the SiteSettings id sequence ahead of the current MAX(id).

    An earlier version forced pk=1 for the global (hospital-less) settings row.
    Inserting an explicit id on PostgreSQL leaves the id sequence behind, so the
    first per-hospital settings row could collide on id=1
    ("duplicate key value violates unique constraint ... _pkey"). This one-time
    resync fixes any deployment already in that state. No-op on SQLite (the desktop
    build) since it has no sequences."""
    conn = schema_editor.connection
    if conn.vendor != 'postgresql':
        return
    with conn.cursor() as cur:
        cur.execute("""
            SELECT setval(
                pg_get_serial_sequence('user_mgmt_sitesettings', 'id'),
                GREATEST(COALESCE((SELECT MAX(id) FROM user_mgmt_sitesettings), 1), 1),
                true
            )
        """)


class Migration(migrations.Migration):

    dependencies = [
        ('user_mgmt', '0007_sitesettings_hospital'),
    ]

    operations = [
        migrations.RunPython(resync_sequence, migrations.RunPython.noop),
    ]
