from decimal import Decimal
from django.db import migrations


def backfill(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")
    for sale in Sale.objects.prefetch_related("items").all():
        gross = Decimal("0.00")
        for item in sale.items.all():
            gross += (item.unit_price or Decimal("0.00")) * item.quantity - (item.discount or Decimal("0.00"))
        # legacy sales had no stored totals; assume fully paid cash retail
        sale.subtotal = gross
        sale.total = gross
        sale.paid = gross
        sale.save(update_fields=["subtotal", "total", "paid"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0002_alter_saleitem_unique_together_sale_cashier_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
