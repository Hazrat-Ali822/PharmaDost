"""Step 1 of 3 — schema only: park the old text column and add the FK.

Moving the four ward models onto the per-hospital `hr.Shift` is deliberately
split across three migrations, and the split is **not** cosmetic. Doing it in
one went like this on PostgreSQL:

    django.db.utils.OperationalError: cannot ALTER TABLE "hr_shift"
    because it has pending trigger events

Each migration runs in one transaction. Writing rows (the data step) leaves
deferred foreign-key trigger events queued, and PostgreSQL then refuses any DDL
that touches the tables those events reference — so the schema changes that came
after the data step in the same migration could not run. SQLite has no such
concept, which is why the whole suite passed and the failure only appeared on
the real database. **Keep DDL and data in separate migrations.**

The other half of the fix is that the old CharField is *renamed out of the way*
and the FK is created under the final name, rather than adding a temporary FK
and renaming it afterwards. Renaming an FK column means dropping and recreating
its constraint — more DDL on `hr_shift`, and exactly the operation that failed.
Renaming a plain text column is a bare `ALTER TABLE ... RENAME COLUMN`.

    0010  this file   schema: drop constraints, rename old column, add the FK
    0011              data:   create each hospital's shifts, re-point every row
    0012              schema: drop the old column, restore constraints/ordering
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0003_shift'),
        ('ipd', '0009_shifthandover_nursingnote_caretask'),
    ]

    operations = [
        # The constraints name `shift`; they have to come off before the column
        # underneath them is renamed, and they go back on in 0012.
        migrations.RemoveConstraint(model_name='nurseshift', name='uniq_nurse_date_shift'),
        migrations.RemoveConstraint(model_name='patientallocation',
                                    name='uniq_admission_date_shift'),

        migrations.RenameField(model_name='nurseshift', old_name='shift',
                               new_name='legacy_shift'),
        migrations.RenameField(model_name='patientallocation', old_name='shift',
                               new_name='legacy_shift'),
        migrations.RenameField(model_name='nursingnote', old_name='shift',
                               new_name='legacy_shift'),
        migrations.RenameField(model_name='shifthandover', old_name='shift',
                               new_name='legacy_shift'),

        migrations.AddField(
            model_name='nurseshift', name='shift',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='nurse_shifts', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='patientallocation', name='shift',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='allocations', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='nursingnote', name='shift',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='hr.shift'),
        ),
        migrations.AddField(
            model_name='shifthandover', name='shift',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='hr.shift',
                                    help_text='The shift being handed over'),
        ),
    ]
