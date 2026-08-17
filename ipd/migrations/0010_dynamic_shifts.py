"""Move the four shift-bearing ward models onto the per-hospital `hr.Shift`.

The shift used to be a CharField holding one of three hardcoded codes with three
hardcoded times, so no hospital could rename a shift, move night duty an hour,
run four shifts, or run one. This clones the old three into every hospital's own
list — the same shape as `lab/0009` and `imaging/0006`, and for the same reason:
stamping every existing row with one hospital's shift would be arbitrary, and
leaving them unmapped would empty every roster ever built.

Order matters. The unique constraints name `shift`, so they come off before the
column does and go back on after the rename.
"""
from datetime import time

from django.db import migrations, models
import django.db.models.deletion


# The values the CharField used to carry, and the times the code used to assume.
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
    # install — whose rows are `hospital = NULL` and which must keep working.
    targets = list(Hospital.objects.values_list('id', flat=True)) + [None]
    by_code = {}
    for hid in targets:
        for code, (name, start, end, order) in OLD.items():
            obj, _ = Shift.objects.get_or_create(
                hospital_id=hid, name=name,
                defaults={'start_time': start, 'end_time': end, 'order': order})
            by_code[(hid, code)] = obj.id

    for model_name in MODELS:
        Model = apps.get_model('ipd', model_name)
        for row in Model.objects.all().iterator():
            code = (row.shift or '').upper()
            if not code:
                continue
            # Fall back to the hospital-less set for a row whose hospital was
            # never stamped, rather than dropping the shift off the record.
            shift_id = by_code.get((row.hospital_id, code)) or by_code.get((None, code))
            if shift_id:
                Model.objects.filter(pk=row.pk).update(shift_new_id=shift_id)


def backwards(apps, schema_editor):
    """Write the shift's name back into the old text column.

    A hospital that has since renamed or added shifts cannot be represented by
    three fixed codes, so anything unrecognised comes back blank rather than
    being forced into the wrong one.
    """
    to_code = {name: code for code, (name, *_rest) in OLD.items()}
    for model_name in MODELS:
        Model = apps.get_model('ipd', model_name)
        for row in Model.objects.select_related('shift_new').iterator():
            name = row.shift_new.name if row.shift_new_id else ''
            Model.objects.filter(pk=row.pk).update(shift=to_code.get(name, ''))


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0003_shift'),
        ('ipd', '0009_shifthandover_nursingnote_caretask'),
    ]

    operations = [
        migrations.RemoveConstraint(model_name='nurseshift', name='uniq_nurse_date_shift'),
        migrations.RemoveConstraint(model_name='patientallocation', name='uniq_admission_date_shift'),

        migrations.AddField(
            model_name='nurseshift', name='shift_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='nurse_shifts', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='patientallocation', name='shift_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='allocations', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='nursingnote', name='shift_new',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='shifthandover', name='shift_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='hr.shift',
                                    help_text='The shift being handed over'),
        ),

        migrations.RunPython(forwards, backwards),

        migrations.RemoveField(model_name='nurseshift', name='shift'),
        migrations.RemoveField(model_name='patientallocation', name='shift'),
        migrations.RemoveField(model_name='nursingnote', name='shift'),
        migrations.RemoveField(model_name='shifthandover', name='shift'),

        migrations.RenameField(model_name='nurseshift', old_name='shift_new', new_name='shift'),
        migrations.RenameField(model_name='patientallocation', old_name='shift_new', new_name='shift'),
        migrations.RenameField(model_name='nursingnote', old_name='shift_new', new_name='shift'),
        migrations.RenameField(model_name='shifthandover', old_name='shift_new', new_name='shift'),

        migrations.AlterModelOptions(name='nurseshift', options={'ordering': ('-date', 'shift__order')}),
        migrations.AlterModelOptions(name='patientallocation', options={'ordering': ('-date', 'shift__order')}),

        migrations.AddConstraint(
            model_name='nurseshift',
            constraint=models.UniqueConstraint(fields=('nurse', 'date', 'shift'),
                                               name='uniq_nurse_date_shift'),
        ),
        migrations.AddConstraint(
            model_name='patientallocation',
            constraint=models.UniqueConstraint(fields=('admission', 'date', 'shift'),
                                               name='uniq_admission_date_shift'),
        ),
    ]
