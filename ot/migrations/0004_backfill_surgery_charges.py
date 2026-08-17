"""Give existing surgery records the charge they were actually billed.

Before this, one surgery meant one `standard_charge` on the procedure and no
figure on the record at all. The new fields default to 0, so without a backfill
every historical operation would display as free and drop out of the OT profit
figures — the invoices are still there and still correct, but the record beside
them would disagree with the bill.

Only `surgeon_charge` is filled: that *is* what the old single charge covered.
Theatre, anaesthesia and consumables stay 0 because they genuinely were not
billed separately — inventing a split would be making up history.

Irreversible on purpose: going back would have to guess which of these figures
was original and which this migration wrote.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    SurgeryRecord = apps.get_model('ot', 'SurgeryRecord')
    for record in SurgeryRecord.objects.select_related('procedure').iterator():
        if record.surgeon_charge:
            continue
        record.surgeon_charge = record.procedure.standard_charge
        record.cost_price = record.procedure.cost_price
        record.save(update_fields=['surgeon_charge', 'cost_price'])


class Migration(migrations.Migration):

    dependencies = [('ot', '0003_surgery_itemised_charges')]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
