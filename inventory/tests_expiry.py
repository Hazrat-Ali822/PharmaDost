"""Pharmacy safety: expired stock is never dispensed, COGS is captured, returns of
expired stock are quarantined, and aggregate/batch drift can be reconciled."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from inventory.models import Medicine
from sales.services import create_sale, return_sale


class PharmacySafetyTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='ph@x.com', password='pw', role='PHARMACIST')
        self.today = timezone.localdate()
        # a medicine whose aggregate expiry is far out, but with mixed batches
        self.med = Medicine.objects.create(
            name='Amoxil', brand='GSK', price=Decimal('50'),
            expiry_date=self.today + timedelta(days=365), quantity=0)

    def _batch(self, qty, days, cost='20'):
        return self.med.add_stock(qty, expiry_date=self.today + timedelta(days=days),
                                  cost_price=Decimal(cost))

    def test_expired_batch_is_not_dispensed(self):
        self._batch(10, days=-1, cost='20')   # already expired
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 10)          # on-hand
        self.assertEqual(self.med.sellable_quantity, 0)  # but nothing sellable
        with self.assertRaises(ValueError):
            create_sale(items=[{"medicine_id": self.med.id, "quantity": 1}], cashier=self.user)

    def test_fefo_skips_expired_and_records_cost(self):
        self._batch(5, days=-1, cost='18')    # expired — must be skipped
        self._batch(5, days=200, cost='25')   # in-date — should be used
        self.med.refresh_from_db()
        self.assertEqual(self.med.sellable_quantity, 5)
        sale = create_sale(items=[{"medicine_id": self.med.id, "quantity": 3}], cashier=self.user)
        item = sale.items.first()
        self.assertEqual(item.cost_price, Decimal('25.00'))   # COGS from the in-date batch
        self.assertEqual(item.line_cost, Decimal('75.00'))
        self.med.refresh_from_db()
        self.assertEqual(self.med.sellable_quantity, 2)       # 5 in-date - 3 sold

    def test_cannot_oversell_beyond_indate_stock(self):
        self._batch(2, days=100)
        self._batch(10, days=-5)   # expired, not counted
        with self.assertRaises(ValueError):
            create_sale(items=[{"medicine_id": self.med.id, "quantity": 5}], cashier=self.user)

    def test_return_of_expired_is_quarantined(self):
        b = self._batch(4, days=100)
        sale = create_sale(items=[{"medicine_id": self.med.id, "quantity": 4}], cashier=self.user)
        # batch expires before the return happens
        b.expiry_date = self.today - timedelta(days=1)
        b.save(update_fields=['expiry_date'])
        result = return_sale(sale, by_user=self.user)
        self.assertEqual(result.quarantined_qty, 4)
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 4)            # on-hand restored
        self.assertEqual(self.med.sellable_quantity, 0)   # but not resellable

    def test_reconcile_fixes_drift(self):
        self._batch(6, days=100)
        # simulate drift: aggregate says 10 but batches sum to 6
        self.med.quantity = 10
        self.med.save(update_fields=['quantity'])
        self.assertEqual(self.med.stock_drift, 4)
        corrected = self.med.reconcile_quantity()
        self.assertEqual(corrected, 4)
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 6)
        self.assertEqual(self.med.stock_drift, 0)
