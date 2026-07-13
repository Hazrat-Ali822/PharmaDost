from decimal import Decimal
from django.db import migrations


def backfill(apps, schema_editor):
    PurchaseOrder = apps.get_model("inventory", "PurchaseOrder")
    for po in PurchaseOrder.objects.prefetch_related("items").all():
        total = Decimal("0.00")
        for it in po.items.all():
            total += (it.cost_price or Decimal("0.00")) * it.quantity
        # assume legacy purchases were fully settled so no phantom payable appears
        po.total = total
        po.paid = total
        po.save(update_fields=["total", "paid"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_purchaseorder_paid_purchaseorder_total"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
