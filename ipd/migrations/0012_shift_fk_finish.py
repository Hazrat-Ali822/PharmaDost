"""Step 3 of 3 — schema only: drop the old column and restore the constraints.

Separate from the data step in 0011 for the reason given at the top of
`0010_shift_fk_add`: DDL in the same transaction as the writes fails on
PostgreSQL with "cannot ALTER TABLE ... because it has pending trigger events".
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ipd', '0011_shift_fk_data'),
    ]

    operations = [
        migrations.RemoveField(model_name='nurseshift', name='legacy_shift'),
        migrations.RemoveField(model_name='patientallocation', name='legacy_shift'),
        migrations.RemoveField(model_name='nursingnote', name='legacy_shift'),
        migrations.RemoveField(model_name='shifthandover', name='legacy_shift'),

        migrations.AlterModelOptions(name='nurseshift',
                                     options={'ordering': ('-date', 'shift__order')}),
        migrations.AlterModelOptions(name='patientallocation',
                                     options={'ordering': ('-date', 'shift__order')}),

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
