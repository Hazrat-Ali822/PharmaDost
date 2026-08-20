"""What the shop paid for a medicine had nowhere to be written.

`Medicine` carried `price` and `wholesale_price` — both selling prices — and no
purchase price at all, so "Add medicine" asked what to sell a tablet for and
never what it cost. Profit was therefore whatever the two silent defaults
happened to produce, and both were wrong in a way that looked like an answer:

* Stock added straight from the Add-medicine form created no batch, so the sale
  froze `SaleItem.cost_price = 0` and the item reported **100% margin**.
* `Medicine.add_stock()` defaulted an unspecified cost to `self.price` — the
  SELLING price — so a batch stocked that way reported **exactly zero profit**.

Backfill is deliberately conservative. A batch whose `cost_price` equals the
medicine's selling price is almost certainly that second default rather than a
real purchase price, and copying it into the new column would make a wrong
number look deliberate. So only a batch cost that *differs* from the selling
price is recovered; everything else stays 0, which now reads as "not recorded"
and is reported as such instead of being counted as free stock.

Schema first, then data — never the other way round in one migration (see the
`pending trigger events` note in CLAUDE.md).
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


def backfill_cost_from_batches(apps, schema_editor):
    Medicine = apps.get_model('inventory', 'Medicine')
    StockBatch = apps.get_model('inventory', 'StockBatch')

    # Most recent batch per medicine, cheaply: one pass, newest last wins.
    latest = {}
    for batch in (StockBatch.objects
                  .order_by('received_at', 'pk')
                  .values_list('medicine_id', 'cost_price')):
        medicine_id, cost = batch
        if cost and cost > Decimal('0.00'):
            latest[medicine_id] = cost

    if not latest:
        return

    updates = []
    for med in Medicine.objects.filter(pk__in=latest).only('pk', 'price', 'cost_price'):
        cost = latest[med.pk]
        # Equal to the selling price => the old `add_stock` default, not a fact.
        if cost == med.price:
            continue
        med.cost_price = cost
        updates.append(med)

    if updates:
        Medicine.objects.bulk_update(updates, ['cost_price'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0012_alter_medicine_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='medicine',
            name='cost_price',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'),
                                      max_digits=10,
                                      validators=[MinValueValidator(0)]),
        ),
        migrations.RunPython(backfill_cost_from_batches,
                             migrations.RunPython.noop),
    ]
