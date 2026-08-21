from django.db import migrations
import uuid


def generate_unique_tokens(apps, schema_editor):
    Patient = apps.get_model('patients', 'Patient')
    # Assign a unique, individual UUID to every patient
    for p in Patient.objects.all():
        p.portal_token = uuid.uuid4()
        p.save(update_fields=['portal_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0010_patient_portal_token'),
    ]

    operations = [
        migrations.RunPython(generate_unique_tokens, reverse_code=migrations.RunPython.noop),
    ]
