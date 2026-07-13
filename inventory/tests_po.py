from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from suppliers.models import Supplier
from inventory.models import Medicine, PurchaseRequest, PurchaseOrder, StockBatch


class PurchaseOrderFlowTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='a@t.com', password='pw', role='ADMIN')
        self.sup = Supplier.objects.create(name='ABC Distributors')
        exp = date.today() + timedelta(days=365)
        # low stock (5 < reorder 20) and healthy stock
        self.low = Medicine.objects.create(name='Panadol 500mg', price=Decimal('10'),
                                           quantity=5, reorder_level=20, expiry_date=exp, barcode='8964001')
        self.ok = Medicine.objects.create(name='Augmentin 625', price=Decimal('200'),
                                          quantity=100, reorder_level=10, expiry_date=exp)
        self.c = Client(SERVER_NAME='127.0.0.1')
        self.c.force_login(self.admin)

    def _new(self):
        self.c.post(reverse('po_create'), {'supplier': self.sup.id})
        return PurchaseRequest.objects.latest('id')

    def test_paste_and_autofill(self):
        req = self._new()
        # paste
        self.c.post(reverse('po_paste', args=[req.id]), {'bulk': 'Augmentin 625 40'})
        req.refresh_from_db()
        self.assertEqual(req.item_count, 1)
        # auto-suggest low stock: Panadol (5<20) → suggested 15
        self.c.post(reverse('po_autofill', args=[req.id]), {})
        req.refresh_from_db()
        self.assertEqual(req.item_count, 2)
        low_item = req.items.get(medicine=self.low)
        self.assertEqual(low_item.quantity, 15)   # 20 - 5

    def test_receive_adds_stock_and_supplier_balance(self):
        req = self._new()
        self.c.post(reverse('po_add', args=[req.id]), {'medicine': 'Panadol 500mg', 'quantity': '100'})
        exp = (date.today() + timedelta(days=400)).isoformat()
        item = req.items.first()
        r = self.c.post(reverse('po_receive', args=[req.id]), {
            'invoice_number': 'INV-9',
            'paid': '500',
            f'recv_qty_{item.id}': '100',
            f'recv_cost_{item.id}': '7.50',
            f'recv_exp_{item.id}': exp,
            f'recv_batch_{item.id}': 'B-1',
        })
        self.assertEqual(r.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, 'RECEIVED')
        self.assertIsNotNone(req.purchase_order_id)
        # stock increased 5 -> 105
        self.low.refresh_from_db()
        self.assertEqual(self.low.quantity, 105)
        # a batch was created with the right cost/expiry
        batch = StockBatch.objects.filter(medicine=self.low, batch_number='B-1').first()
        self.assertIsNotNone(batch)
        self.assertEqual(batch.cost_price, Decimal('7.50'))
        # PO total = 100*7.50 = 750, paid 500 → supplier khata +250
        po = PurchaseOrder.objects.get(pk=req.purchase_order_id)
        self.assertEqual(po.total, Decimal('750.00'))
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.balance, Decimal('250.00'))

    def test_received_order_is_locked(self):
        req = self._new()
        self.c.post(reverse('po_add', args=[req.id]), {'medicine': 'Panadol 500mg', 'quantity': '10'})
        item = req.items.first()
        self.c.post(reverse('po_receive', args=[req.id]), {
            f'recv_qty_{item.id}': '10', f'recv_cost_{item.id}': '5',
            f'recv_exp_{item.id}': (date.today() + timedelta(days=100)).isoformat()})
        # a second receive must be refused
        r = self.c.post(reverse('po_receive', args=[req.id]), {})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PurchaseOrder.objects.count(), 1)   # not received twice
