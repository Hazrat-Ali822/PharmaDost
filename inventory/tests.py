from decimal import Decimal
from django.test import TestCase
from datetime import date, timedelta
from suppliers.models import Supplier
from inventory.models import Medicine, StockBatch


class AlertsTest(TestCase):
    def test_low_stock_and_expiring(self):
        s = Supplier.objects.create(name='ACME', phone='000')
        today = date.today()
        m1 = Medicine.objects.create(name='A', brand='B', price=10, quantity=3, expiry_date=today + timedelta(days=10), supplier=s)
        m2 = Medicine.objects.create(name='C', brand='D', price=10, quantity=20, expiry_date=today + timedelta(days=40), supplier=s)
        self.assertIn(m1, Medicine.objects.low_stock(5))
        self.assertIn(m1, Medicine.objects.expiring_soon(30))
        self.assertNotIn(m2, Medicine.objects.expiring_soon(30))

    def test_soft_delete_hides_item_from_default_queries(self):
        s = Supplier.objects.create(name='Soft', phone='111')
        med = Medicine.objects.create(name='SoftMed', brand='B', price=10, quantity=5, expiry_date=date.today() + timedelta(days=20), supplier=s)

        med.is_active = False
        med.save(update_fields=['is_active'])

        self.assertNotIn(med, Medicine.objects.all())
        self.assertEqual(Medicine.objects.filter(pk=med.pk).count(), 0)


class BatchStockTests(TestCase):
    def test_purchase_creates_batch_and_updates_stock(self):
        supplier = Supplier.objects.create(name='Batch Supplier', phone='222')
        med = Medicine.objects.create(name='Panadol', brand='GSK', price=10, quantity=0, expiry_date=date.today() + timedelta(days=120), supplier=supplier)

        batch = med.add_stock(quantity=20, batch_number='B-100', expiry_date=date.today() + timedelta(days=90), cost_price=Decimal('8.50'), supplier=supplier)

        self.assertEqual(med.quantity, 20)
        self.assertEqual(StockBatch.objects.filter(medicine=med).count(), 1)
        self.assertEqual(batch.quantity, 20)
        self.assertEqual(batch.batch_number, 'B-100')