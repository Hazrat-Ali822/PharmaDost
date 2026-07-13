from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from customers.models import Customer
from inventory.models import Medicine
from sales.models import WholesaleOrder, Sale
from sales.wholesale_views import parse_line, match_medicine


class ParseLineTest(TestCase):
    def test_formats(self):
        self.assertEqual(parse_line("Panadol 500mg | 100"), ("Panadol 500mg", 100))
        self.assertEqual(parse_line("Augmentin 625, 50"), ("Augmentin 625", 50))
        self.assertEqual(parse_line("Brufen 400 x 200"), ("Brufen 400", 200))
        self.assertEqual(parse_line("Rigix   90"), ("Rigix", 90))
        self.assertEqual(parse_line("Disprin\t30"), ("Disprin", 30))
        self.assertEqual(parse_line("JustAName"), ("JustAName", 1))
        self.assertIsNone(parse_line("   "))


class WholesaleFlowTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='a@t.com', password='pw', role='ADMIN')
        self.cust = Customer.objects.create(name='Ali Traders', shop_name='Ali Medical', type='WHOLESALE')
        exp = date.today() + timedelta(days=365)
        self.m1 = Medicine.objects.create(name='Panadol 500mg', price=Decimal('10'),
                                          wholesale_price=Decimal('8'), quantity=1000, expiry_date=exp, barcode='8964001')
        self.m2 = Medicine.objects.create(name='Augmentin 625', price=Decimal('200'),
                                          wholesale_price=Decimal('165'), quantity=500, expiry_date=exp)
        self.c = Client(SERVER_NAME='127.0.0.1')
        self.c.force_login(self.admin)

    def _new_order(self):
        self.c.post(reverse('wholesale_order_create'), {'customer': self.cust.id})
        return WholesaleOrder.objects.latest('id')

    def test_match_medicine(self):
        self.assertEqual(match_medicine('8964001'), self.m1)       # barcode
        self.assertEqual(match_medicine('Panadol 500mg'), self.m1)  # exact name
        self.assertEqual(match_medicine('Augmentin'), self.m2)      # unique partial
        self.assertIsNone(match_medicine('Nonexistent'))

    def test_paste_adds_matched_and_flags_unmatched(self):
        o = self._new_order()
        bulk = "Panadol 500mg | 100\nAugmentin 625, 50\nUnknownMed 20"
        self.c.post(reverse('wholesale_order_paste', args=[o.id]), {'bulk': bulk})
        o.refresh_from_db()
        self.assertEqual(o.item_count, 2)                  # 2 matched
        self.assertEqual(o.total_qty, 150)
        # wholesale price applied
        item = o.items.get(medicine=self.m1)
        self.assertEqual(item.unit_price, Decimal('8'))
        self.assertEqual(o.total, Decimal('8')*100 + Decimal('165')*50)

    def test_paste_merges_duplicate_lines(self):
        o = self._new_order()
        self.c.post(reverse('wholesale_order_paste', args=[o.id]),
                    {'bulk': "Panadol 500mg 100\nPanadol 500mg 25"})
        o.refresh_from_db()
        self.assertEqual(o.item_count, 1)
        self.assertEqual(o.items.first().quantity, 125)

    def test_quick_add_and_repeat(self):
        o = self._new_order()
        self.c.post(reverse('wholesale_order_add', args=[o.id]),
                    {'medicine': 'Panadol 500mg — 8964001', 'quantity': '10'})
        o.refresh_from_db()
        self.assertEqual(o.item_count, 1)
        # repeat into a new order for same customer
        o2 = self._new_order()
        self.c.post(reverse('wholesale_order_repeat', args=[o2.id]), {'source': o.id})
        o2.refresh_from_db()
        self.assertEqual(o2.item_count, 1)
        self.assertEqual(o2.items.first().quantity, 10)

    def test_convert_to_bill_deducts_stock(self):
        o = self._new_order()
        self.c.post(reverse('wholesale_order_paste', args=[o.id]),
                    {'bulk': "Panadol 500mg 100\nAugmentin 625 50"})
        r = self.c.post(reverse('wholesale_order_convert', args=[o.id]),
                        {'paid': '0', 'payment_method': 'CREDIT'})
        self.assertEqual(r.status_code, 302)
        o.refresh_from_db()
        self.assertEqual(o.status, 'BILLED')
        self.assertIsNotNone(o.sale_id)
        sale = Sale.objects.get(pk=o.sale_id)
        self.assertEqual(sale.sale_type, Sale.WHOLESALE)
        self.assertEqual(sale.total, Decimal('8')*100 + Decimal('165')*50)
        self.m1.refresh_from_db(); self.m2.refresh_from_db()
        self.assertEqual(self.m1.quantity, 900)   # 1000 - 100
        self.assertEqual(self.m2.quantity, 450)   # 500 - 50

    def test_convert_blocks_when_short_stock(self):
        o = self._new_order()
        self.c.post(reverse('wholesale_order_add', args=[o.id]),
                    {'medicine': 'Panadol 500mg', 'quantity': '99999'})
        self.c.post(reverse('wholesale_order_convert', args=[o.id]),
                    {'paid': '0', 'payment_method': 'CREDIT'})
        o.refresh_from_db()
        self.assertEqual(o.status, 'DRAFT')       # not billed
        self.assertEqual(Sale.objects.count(), 0)
        self.m1.refresh_from_db()
        self.assertEqual(self.m1.quantity, 1000)  # stock untouched
