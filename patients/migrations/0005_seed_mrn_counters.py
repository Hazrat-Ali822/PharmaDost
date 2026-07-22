"""Start each hospital's MRN counter above whatever it has already issued.

Existing patients keep the MRNs on their cards — those are never rewritten. This
only makes sure the first auto-allocated number cannot collide with one that was
typed in by hand before numbering existed.
"""
import re

from django.db import migrations


def seed_counters(apps, schema_editor):
    Patient = apps.get_model('patients', 'Patient')
    SiteSettings = apps.get_model('user_mgmt', 'SiteSettings')

    # Highest trailing number already in use, per hospital (None = single-site).
    highest = {}
    for hospital_id, mrn in Patient.objects.values_list('hospital_id', 'mrn'):
        match = re.search(r'(\d+)\s*$', mrn or '')
        if not match:
            continue
        number = int(match.group(1))
        if number > highest.get(hospital_id, 0):
            highest[hospital_id] = number

    for row in SiteSettings.objects.all():
        reached = highest.get(row.hospital_id, 0)
        if reached > (row.mrn_last_number or 0):
            row.mrn_last_number = reached
            row.save(update_fields=['mrn_last_number'])


def noop(apps, schema_editor):
    """Reversible: the counter is advisory, the unique constraint is the guarantee."""


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0004_alter_patient_mrn_patient_uniq_mrn_per_hospital_and_more'),
        ('user_mgmt', '0010_sitesettings_mrn_last_number_sitesettings_mrn_prefix'),
    ]

    operations = [
        migrations.RunPython(seed_counters, noop),
    ]
