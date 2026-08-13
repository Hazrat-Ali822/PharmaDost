"""Strip a leading title from every stored doctor name.

Names were typed as "Dr. Sara Ahmed", and ~34 templates prefix "Dr." themselves —
so the OPD token slip and the IPD discharge summary, both handed to the patient,
read "Dr. Dr. Sara Ahmed". `Doctor.save()` now normalises this; existing rows need
the same treatment once.
"""
from django.db import migrations

TITLES = ('dr.', 'dr', 'doctor', 'prof.', 'prof', 'professor')


def strip_titles(apps, schema_editor):
    Doctor = apps.get_model('opd', 'Doctor')
    # `all_objects` does not exist on Doctor and `objects` here is the plain
    # historical manager, so this covers every tenant's rows in one pass.
    for doctor in Doctor.objects.all().iterator():
        name = (doctor.full_name or '').strip()
        lowered = name.lower()
        for title in TITLES:
            if lowered.startswith(title + ' '):
                cleaned = name[len(title):].strip()
                if cleaned and cleaned != doctor.full_name:
                    doctor.full_name = cleaned
                    doctor.save(update_fields=['full_name'])
                break


def put_them_back(apps, schema_editor):
    """Not reversible: we cannot know which names carried a title to begin with,
    and re-adding one to all of them would be a different kind of wrong."""


class Migration(migrations.Migration):

    dependencies = [
        ('opd', '0005_doctorschedule_doctoravailabilityoverride_department_and_more'),
    ]

    operations = [
        migrations.RunPython(strip_titles, put_them_back),
    ]
