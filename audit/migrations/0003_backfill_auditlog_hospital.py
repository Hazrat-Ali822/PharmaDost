"""File existing audit rows under the tenant they belong to.

Until now the trail had no hospital at all, so every row is unattributed. Left
as-is they would be invisible to every hospital admin (the manager filters to
their own hospital) while still being readable by nobody else — history the
owner cannot see. The acting user's hospital is the best evidence we have of
who the entry concerns.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    AuditLog = apps.get_model('audit', 'AuditLog')
    User = apps.get_model('accounts', 'User')

    by_user = dict(User.objects.exclude(hospital__isnull=True)
                   .values_list('id', 'hospital_id'))
    if not by_user:
        return
    for user_id, hospital_id in by_user.items():
        (AuditLog.objects
         .filter(user_id=user_id, hospital__isnull=True)
         .update(hospital_id=hospital_id))


def noop(apps, schema_editor):
    """Reversible: clearing the column again loses nothing the rows did not
    already lack."""


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0002_auditlog_hospital'),
        # not 0001_initial — User only grows its `hospital` column here, and the
        # historical model this migration sees is the one at the stated dependency
        ('accounts', '0004_user_hospital'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
