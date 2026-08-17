"""Step 2 of 3 — data only: give every hospital its own shifts and re-point rows.

No schema change may share this migration; see the note at the top of
`0010_shift_fk_add` for what PostgreSQL does when it does.

The clone-per-hospital shape is the one `lab/0009` and `imaging/0006` already
use. Stamping every existing roster with one hospital's shift would be
arbitrary, and leaving them unmapped would be worse: `TenantManager` hides
`hospital IS NULL` from a hospital-scoped user, so every roster, allocation,
note and handover ever written would come back blank.
"""
from datetime import time

from django.db import migrations


# The three values the CharField used to carry, and the times the code assumed.
OLD = {
    'MORNING': ('Morning', time(7, 0), time(14, 0), 0),
    'EVENING': ('Evening', time(14, 0), time(21, 0), 1),
    'NIGHT': ('Night', time(21, 0), time(7, 0), 2),
}

MODELS = ('NurseShift', 'PatientAllocation', 'NursingNote', 'ShiftHandover')


def forwards(apps, schema_editor):
    Shift = apps.get_model('hr', 'Shift')
    Hospital = apps.get_model('saas', 'Hospital')

    # Every tenant gets its own copy, and so does the hospital-less desktop/LAN
    # install, whose rows are `hospital = NULL` and which must keep working.
    targets = list(Hospital.objects.values_list('id', flat=True)) + [None]
    by_code = {}
    for hid in targets:
        for code, (name, start, end, order) in OLD.items():
            obj, _created = Shift.objects.get_or_create(
                hospital_id=hid, name=name,
                defaults={'start_time': start, 'end_time': end, 'order': order})
            by_code[(hid, code)] = obj.id

    for model_name in MODELS:
        Model = apps.get_model('ipd', model_name)
        for row in Model.objects.all().iterator():
            code = (row.legacy_shift or '').upper()
            if not code:
                continue
            # A row whose hospital was never stamped falls back to the
            # hospital-less set rather than losing its shift entirely.
            shift_id = by_code.get((row.hospital_id, code)) or by_code.get((None, code))
            if shift_id:
                Model.objects.filter(pk=row.pk).update(shift_id=shift_id)


def backwards(apps, schema_editor):
    """Write the shift's name back into the old text column.

    A hospital that has since renamed a shift or added a fourth cannot be
    represented by three fixed codes, so anything unrecognised comes back blank
    rather than being forced into the wrong one.
    """
    to_code = {name: code for code, (name, *_rest) in OLD.items()}
    for model_name in MODELS:
        Model = apps.get_model('ipd', model_name)
        for row in Model.objects.select_related('shift').iterator():
            name = row.shift.name if row.shift_id else ''
            Model.objects.filter(pk=row.pk).update(legacy_shift=to_code.get(name, ''))


class Migration(migrations.Migration):

    dependencies = [
        ('ipd', '0010_shift_fk_add'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
