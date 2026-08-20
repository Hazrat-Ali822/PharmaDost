"""Give `Doctor` a hospital of its own, and backfill it.

Doctors were scoped through their linked user account, and most doctors have no
account: the user field on `/opd/doctors/add/` is optional and normally left
blank, because a doctor is a roster entry, not a login. So `scoped_doctors` had
to let user-less rows through to everybody — which meant every hospital saw
every other hospital's doctors on the OPD board, in the booking dropdown and in
the payout CSV.

The backfill walks three sources, most reliable first, because none of them is
available for every row:

1. the linked user's hospital — exact, when there is a user;
2. the department's hospital — `Department` is tenant-scoped, and a doctor is
   almost always filed under one;
3. the hospital of a patient this doctor has actually seen — an appointment is
   proof of where they work.

Anything still unresolved is left NULL, which is correct rather than a guess: a
hospital-less install (the desktop/LAN build) is exactly that case, and a wrong
hospital would hide a real doctor from the people who need them while showing
them to strangers.

Schema then data in one migration is fine — it is DDL *after* DML that
PostgreSQL refuses (CLAUDE.md, "Two databases").
"""
from django.db import migrations, models
import django.db.models.deletion


# Kept in step with `Doctor._TITLES`.
TITLES = ('dr.', 'dr', 'doctor', 'prof.', 'prof', 'professor')


def _strip_title(name):
    name = (name or '').strip()
    for title in TITLES:
        lowered = name.lower()
        if not lowered.startswith(title):
            continue
        rest = name[len(title):]
        if not (title.endswith('.') or not rest or rest[0] in ' .\t'):
            continue
        return rest.lstrip(' .\t')
    return name


def fill_hospital(apps, schema_editor):
    Doctor = apps.get_model('opd', 'Doctor')
    Appointment = apps.get_model('opd', 'Appointment')

    for doctor in Doctor.objects.all().select_related('user', 'department'):
        hospital_id = None
        if doctor.user_id and doctor.user.hospital_id:
            hospital_id = doctor.user.hospital_id
        elif doctor.department_id and doctor.department.hospital_id:
            hospital_id = doctor.department.hospital_id
        else:
            seen = (Appointment.objects
                    .filter(doctor_id=doctor.pk, patient__hospital__isnull=False)
                    .values_list('patient__hospital_id', flat=True)
                    .first())
            hospital_id = seen

        # The same pass repairs a title that is already stored. `Doctor.save()`
        # only stripped "Dr. " with a trailing space, so "Dr.Shariq" — typed
        # without one, which is how it is typed on a phone — kept its title and
        # every screen rendered "Dr. Dr.Shariq", the printed prescription
        # included. Re-saving each row from the app would fix it; nobody is
        # going to, so do it here.
        fixed = _strip_title(doctor.full_name)

        if hospital_id != doctor.hospital_id or fixed != doctor.full_name:
            doctor.hospital_id = hospital_id
            doctor.full_name = fixed
            doctor.save(update_fields=['hospital', 'full_name'])


def unfill_hospital(apps, schema_editor):
    """Reversing drops the column, so there is nothing to undo. The stripped
    titles are deliberately not put back — re-adding "Dr." would be inventing
    data, and the value without it is the correct one either way."""


class Migration(migrations.Migration):

    dependencies = [
        ('saas', '0001_initial'),
        ('opd', '0006_strip_doctor_titles'),
    ]

    operations = [
        migrations.AddField(
            model_name='doctor',
            name='hospital',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='doctors', to='saas.hospital'),
        ),
        migrations.RunPython(fill_hospital, unfill_hospital),
    ]
