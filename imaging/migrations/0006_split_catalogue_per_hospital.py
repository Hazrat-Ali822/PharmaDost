"""Give every hospital its own copy of the scan catalogue.

Same reasoning as `lab/migrations/0009` — see the note there. Imaging needs no
re-pointing pass: `ImagingStudy` stores the modality, name and price on itself
and holds no FK to `ScanType`, which is only the picker.
"""
from django.db import migrations


def clone_per_hospital(apps, schema_editor):
    Hospital = apps.get_model('saas', 'Hospital')
    ScanType = apps.get_model('imaging', 'ScanType')

    hospitals = list(Hospital.objects.all())
    shared = list(ScanType.objects.filter(hospital__isnull=True))
    if not hospitals or not shared:
        return

    ScanType.objects.bulk_create([
        ScanType(modality=s.modality, name=s.name, price=s.price,
                 is_active=s.is_active, hospital=hospital)
        for hospital in hospitals for s in shared
    ])


def noop_reverse(apps, schema_editor):
    """Not reversible in data terms — see lab/migrations/0009."""


class Migration(migrations.Migration):

    dependencies = [
        ('imaging', '0005_scantype_hospital'),
        ('saas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(clone_per_hospital, noop_reverse),
    ]
