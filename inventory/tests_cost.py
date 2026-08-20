"""What the shop paid, and what happens to profit when nobody records it.

`Medicine` had two selling prices and no purchase price, so "Add medicine"
never asked what a tablet cost. Profit was then decided by two silent defaults,
and both produced a confident wrong number rather than an obvious blank:

* Stock typed into the Add-medicine form created **no batch**, so `reduce_stock`
  took its legacy aggregate path, the sale froze `SaleItem.cost_price = 0`, and
  the profit report counted the whole selling price as margin.
* `Medicine.add_stock()` defaulted a missing cost to `self.price` — the
  *selling* price — so anything stocked that way reported **exactly zero
  profit** for ever.

Nothing on any screen distinguished either case from a real answer, which is
what makes it worth a test file of its own: the failure mode is a plausible
number, not an error.

    python manage.py test inventory.tests_cost --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Medicine
from suppliers.models import Supplier


def _exp():
    return date.today() + timedelta(days=365)


class AddingAMedicineRecordsWhatItCostTest(TestCase):
    """The reported case: adding a tablet never asked the purchase price."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.client = Client()
        self.client.force_login(self.admin)

    def _add(self, **overrides):
        data = {
            'name': 'Panadol', 'units_per_pack': '1',
            'cost_price': '8', 'price': '10', 'wholesale_price': '0',
            'reorder_level': '10', 'quantity': '100',
            'expiry_date': _exp().isoformat(),
        }
        data.update(overrides)
        return self.client.post(reverse('medicine_add'), data)

    def test_the_form_asks_for_it(self):
        body = self.client.get(reverse('medicine_add')).content.decode()
        self.assertIn('cost_price', body)
        self.assertIn('what you paid', body.lower())

    def test_it_is_stored(self):
        self._add()
        self.assertEqual(Medicine.objects.get(name='Panadol').cost_price, Decimal('8.00'))

    def test_the_opening_stock_becomes_a_real_batch(self):
        """Not just the denormalised aggregate. A quantity with no batch behind
        it has no cost and no expiry of its own, which is how the whole selling
        price came to be reported as profit."""
        self._add()
        med = Medicine.objects.get(name='Panadol')
        self.assertEqual(med.quantity, 100)
        batch = med.batches.get()
        self.assertEqual(batch.quantity, 100)
        self.assertEqual(batch.cost_price, Decimal('8.00'))
        self.assertEqual(batch.expiry_date, _exp())

    def test_the_opening_stock_is_not_counted_twice(self):
        """`MedicineForm` writes `quantity` and `add_stock` increments it."""
        self._add(quantity='40')
        med = Medicine.objects.get(name='Panadol')
        self.assertEqual(med.quantity, 40)
        self.assertEqual(med.batch_quantity, 40)
        self.assertEqual(med.stock_drift, 0)

    def test_no_opening_stock_creates_no_batch(self):
        self._add(quantity='0')
        self.assertEqual(Medicine.objects.get(name='Panadol').batches.count(), 0)

    def test_the_cost_may_be_left_blank(self):
        """It must stay optional — the offline `medicine` handler replays
        payloads written before the field existed, and a required field would
        reject them permanently."""
        resp = self._add(cost_price='')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Medicine.objects.get(name='Panadol').cost_price, Decimal('0.00'))


class AnUnknownCostIsNotTheSellingPriceTest(TestCase):
    """`add_stock` used to default the cost to `self.price`."""

    def test_add_stock_does_not_invent_a_cost_from_the_selling_price(self):
        med = Medicine.objects.create(name='M', price=Decimal('10'), expiry_date=_exp())
        batch = med.add_stock(10, expiry_date=_exp())
        self.assertEqual(batch.cost_price, Decimal('0.00'),
                         'an unknown cost must stay unknown — defaulting it to '
                         'the selling price reports exactly zero profit for ever')

    def test_add_stock_uses_the_medicine_purchase_price_when_it_has_one(self):
        med = Medicine.objects.create(name='M', cost_price=Decimal('6'),
                                      price=Decimal('10'), expiry_date=_exp())
        self.assertEqual(med.add_stock(10, expiry_date=_exp()).cost_price, Decimal('6.00'))

    def test_an_explicit_cost_still_wins(self):
        med = Medicine.objects.create(name='M', cost_price=Decimal('6'),
                                      price=Decimal('10'), expiry_date=_exp())
        batch = med.add_stock(10, expiry_date=_exp(), cost_price=Decimal('5.50'))
        self.assertEqual(batch.cost_price, Decimal('5.50'))


class TheSaleFreezesTheRealCostTest(TestCase):
    """End to end: add a tablet at 8, sell at 10, the margin must be 2."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.client.force_login(self.admin)
        self.client.post(reverse('medicine_add'), {
            'name': 'Panadol', 'units_per_pack': '1',
            'cost_price': '8', 'price': '10', 'wholesale_price': '0',
            'reorder_level': '10', 'quantity': '100',
            'expiry_date': _exp().isoformat(),
        })
        self.med = Medicine.objects.get(name='Panadol')

    def test_the_margin_is_two_rupees_a_tablet(self):
        from sales.services import create_sale
        sale = create_sale(cashier=self.admin, items=[
            {'medicine_id': self.med.pk, 'quantity': 5},
        ])
        item = sale.items.get()
        self.assertEqual(item.cost_price, Decimal('8.00'))
        self.assertEqual(item.line_profit, Decimal('10.00'))   # (10 - 8) x 5

    def test_the_margin_is_visible_on_the_medicine_itself(self):
        self.assertEqual(self.med.unit_profit, Decimal('2.00'))
        self.assertEqual(round(self.med.margin_percent), 20)

    def test_an_unpriced_medicine_reports_no_margin_rather_than_full_margin(self):
        med = Medicine.objects.create(name='X', price=Decimal('10'), expiry_date=_exp())
        self.assertFalse(med.has_cost)
        self.assertIsNone(med.unit_profit)
        self.assertIsNone(med.margin_percent)


class TheProfitReportSaysWhenItCannotKnowTest(TestCase):
    """An unrecorded cost arrives in the subtraction as zero, so the profit is
    overstated and nothing on the page said so."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')

    def _sell(self, med, qty):
        from sales.services import create_sale
        return create_sale(cashier=self.admin, items=[{'medicine_id': med.pk, 'quantity': qty}])

    def _report(self):
        from reports.utils import module_profit_data
        today = date.today()
        return module_profit_data(today, today)

    def test_a_priced_medicine_reports_a_real_margin_and_no_gap(self):
        med = Medicine.objects.create(name='P', cost_price=Decimal('8'),
                                      price=Decimal('10'), expiry_date=_exp())
        med.add_stock(50, expiry_date=_exp())
        self._sell(med, 10)

        rows, totals = self._report()
        pharmacy = next(r for r in rows if r['key'] == 'PHARMACY')
        self.assertEqual(pharmacy['cost'], Decimal('80.00'))
        self.assertEqual(pharmacy['profit'], Decimal('20.00'))
        self.assertEqual(totals['cost_gap'], Decimal('0.00'))
        self.assertFalse(totals['overstated'])

    def test_an_unpriced_medicine_is_named_rather_than_counted_as_pure_profit(self):
        med = Medicine.objects.create(name='U', price=Decimal('10'), expiry_date=_exp())
        med.add_stock(50, expiry_date=_exp())
        self._sell(med, 10)

        rows, totals = self._report()
        pharmacy = next(r for r in rows if r['key'] == 'PHARMACY')
        self.assertEqual(pharmacy['cost'], Decimal('0.00'))
        self.assertEqual(totals['cost_gap'], Decimal('100.00'),
                         'the revenue whose cost is unknown has to be reported')
        self.assertTrue(totals['overstated'])
        self.assertIn('no purchase price', pharmacy['note'])

    def test_the_page_prints_the_warning(self):
        med = Medicine.objects.create(name='U', price=Decimal('10'), expiry_date=_exp())
        med.add_stock(50, expiry_date=_exp())
        self._sell(med, 10)

        self.client.force_login(self.admin)
        body = self.client.get(reverse('module_profit_report')).content.decode()
        self.assertIn('higher than the truth', body)


class TheMedicineListShowsWhatIsMissingTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.client.force_login(self.admin)
        Medicine.objects.create(name='Priced', cost_price=Decimal('8'),
                                price=Decimal('10'), expiry_date=_exp())
        Medicine.objects.create(name='Unpriced', price=Decimal('10'), expiry_date=_exp())

    def test_it_counts_them(self):
        body = self.client.get(reverse('medicine_list')).content.decode()
        self.assertIn('no purchase price', body)
        self.assertIn('Show them', body)

    def test_the_filter_shows_only_those(self):
        body = self.client.get(reverse('medicine_list'), {'missing_cost': '1'}).content.decode()
        self.assertIn('Unpriced', body)
        self.assertNotIn('>Priced<', body)

    def test_the_margin_column_is_there(self):
        body = self.client.get(reverse('medicine_list')).content.decode()
        self.assertIn('<th>Cost</th>', body)
        self.assertIn('<th>Margin</th>', body)


class ThePurchaseScreenDoesNotGuessEitherTest(TestCase):
    """A blank cost box on the purchase screen fell back to `med.price`."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.client.force_login(self.admin)
        self.sup = Supplier.objects.create(name='S', phone='03001234567')
        self.med = Medicine.objects.create(name='M', price=Decimal('10'), expiry_date=_exp())

    def test_a_blank_cost_is_not_the_selling_price(self):
        self.client.post(reverse('purchase_create'), {
            'supplier': str(self.sup.pk), 'invoice_number': 'INV-1',
            'medicine_id[]': [str(self.med.pk)], 'quantity[]': ['10'],
            'cost_price[]': [''], 'expiry_date[]': [_exp().isoformat()],
        })
        batch = self.med.batches.get()
        self.assertNotEqual(batch.cost_price, Decimal('10.00'),
                            'falling back to the selling price reports the whole '
                            'purchase at exactly zero profit')
        self.assertEqual(batch.cost_price, Decimal('0.00'))

    def test_a_blank_cost_falls_back_to_the_medicines_own_purchase_price(self):
        self.med.cost_price = Decimal('7')
        self.med.save(update_fields=['cost_price'])
        self.client.post(reverse('purchase_create'), {
            'supplier': str(self.sup.pk), 'invoice_number': 'INV-2',
            'medicine_id[]': [str(self.med.pk)], 'quantity[]': ['10'],
            'cost_price[]': [''], 'expiry_date[]': [_exp().isoformat()],
        })
        self.assertEqual(self.med.batches.get().cost_price, Decimal('7.00'))
