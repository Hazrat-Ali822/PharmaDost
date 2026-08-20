"""What can be recovered from records written before the purchase price existed.

The owner's question, and it has three separate answers rather than one:

* **The master data — yes.** Wherever a purchase actually went through the
  Purchase screen or a Purchase Order, the buyer typed a real cost and it is on
  the `StockBatch`. `inventory/0013` recovers those into `Medicine.cost_price`.
  What it deliberately does *not* recover is a batch whose cost equals the
  selling price: that is the old `add_stock` default, not a fact.
* **Filling in the rest — yes, and it has to be practical.** A 200-line
  catalogue cannot be fixed through Edit-and-save one medicine at a time, so
  `/medicines/?missing_cost=1` is a bulk editor. A warning nobody can act on is
  just a complaint.
* **The past sales themselves — no.** What a tablet cost the day it was sold was
  never written down. The report offers an estimate at today's purchase prices
  and stores nothing: writing that into `SaleItem.cost_price` would turn a guess
  into a record, which is the exact failure this whole change exists to undo.

    python manage.py test tests.test_cost_recovery --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Medicine
from sales.services import create_sale


def _future():
    return date.today() + timedelta(days=365)


class TheMigrationRecoversOnlyRealCostsTest(TestCase):
    """`inventory/0013`'s backfill rule, re-run against the same conditions."""

    def _backfill(self):
        """The migration body, applied to the live models.

        Importing the migration and calling its function directly keeps this
        honest: if the rule in the migration changes, this test changes with it
        rather than quietly testing a copy.
        """
        from importlib import import_module

        from django.apps import apps
        mod = import_module('inventory.migrations.0013_medicine_cost_price')
        mod.backfill_cost_from_batches(apps, None)

    def test_a_cost_the_buyer_typed_is_recovered(self):
        med = Medicine.objects.create(name='A', price=Decimal('10'), expiry_date=_future())
        med.add_stock(10, expiry_date=_future(), cost_price=Decimal('6'))
        med.cost_price = Decimal('0.00')
        med.save(update_fields=['cost_price'])

        self._backfill()
        med.refresh_from_db()
        self.assertEqual(med.cost_price, Decimal('6.00'))

    def test_a_batch_cost_equal_to_the_selling_price_is_not_recovered(self):
        """That is the old `add_stock` default. Copying it forward would make a
        wrong number look deliberate."""
        med = Medicine.objects.create(name='B', price=Decimal('10'), expiry_date=_future())
        med.add_stock(10, expiry_date=_future(), cost_price=Decimal('10'))
        med.cost_price = Decimal('0.00')
        med.save(update_fields=['cost_price'])

        self._backfill()
        med.refresh_from_db()
        self.assertEqual(med.cost_price, Decimal('0.00'))

    def test_the_newest_batch_wins(self):
        med = Medicine.objects.create(name='C', price=Decimal('10'), expiry_date=_future())
        med.add_stock(5, expiry_date=_future(), cost_price=Decimal('6'))
        med.add_stock(5, expiry_date=_future(), cost_price=Decimal('7'))
        med.cost_price = Decimal('0.00')
        med.save(update_fields=['cost_price'])

        self._backfill()
        med.refresh_from_db()
        self.assertEqual(med.cost_price, Decimal('7.00'))


class FillingInTheRestHasToBePracticalTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.client.force_login(self.admin)
        self.a = Medicine.objects.create(name='Alpha', price=Decimal('10'), expiry_date=_future())
        self.b = Medicine.objects.create(name='Beta', price=Decimal('20'), expiry_date=_future())
        self.priced = Medicine.objects.create(name='Gamma', cost_price=Decimal('5'),
                                              price=Decimal('9'), expiry_date=_future())

    def test_the_filtered_page_is_editable(self):
        body = self.client.get(reverse('medicine_list'), {'missing_cost': '1'}).content.decode()
        self.assertIn(f'name="cost_{self.a.pk}"', body)
        self.assertIn('Save purchase prices', body)

    def test_the_ordinary_page_is_not(self):
        body = self.client.get(reverse('medicine_list')).content.decode()
        self.assertNotIn('Save purchase prices', body)

    def test_one_save_prices_the_whole_page(self):
        self.client.post(reverse('medicine_list') + '?missing_cost=1', {
            'bulk_cost': '1',
            f'cost_{self.a.pk}': '7.50',
            f'cost_{self.b.pk}': '13',
        })
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.cost_price, Decimal('7.50'))
        self.assertEqual(self.b.cost_price, Decimal('13.00'))

    def test_a_box_left_blank_stays_not_recorded(self):
        """Blank must not be read as zero — that is the whole distinction."""
        self.client.post(reverse('medicine_list') + '?missing_cost=1', {
            'bulk_cost': '1',
            f'cost_{self.a.pk}': '',
            f'cost_{self.b.pk}': '13',
        })
        self.a.refresh_from_db()
        self.assertEqual(self.a.cost_price, Decimal('0.00'))
        self.assertFalse(self.a.has_cost)

    def test_rubbish_is_ignored_rather_than_crashing(self):
        self.client.post(reverse('medicine_list') + '?missing_cost=1', {
            'bulk_cost': '1',
            f'cost_{self.a.pk}': 'abc',
            f'cost_{self.b.pk}': '-4',
        })
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.cost_price, Decimal('0.00'))
        self.assertEqual(self.b.cost_price, Decimal('0.00'))

    def test_an_id_from_another_page_of_the_same_catalogue_is_allowed(self):
        """Deliberate: the list is paginated, and the admin may price any of
        their own medicines. The boundary that matters is the tenant, asserted
        below — not which page the row happened to be rendered on."""
        other = Medicine.objects.create(name='Delta', price=Decimal('1'),
                                        expiry_date=_future())
        self.client.post(reverse('medicine_list') + '?missing_cost=1', {
            'bulk_cost': '1', f'cost_{other.pk}': '99',
        })
        other.refresh_from_db()
        self.assertEqual(other.cost_price, Decimal('99.00'))

    def test_it_cannot_reach_another_hospitals_medicine(self):
        from saas.models import Hospital
        from saas.utils import clear_current_hospital, set_current_hospital

        theirs_h = Hospital.objects.create(name='Other', slug='other',
                                           expiry_date=_future())
        set_current_hospital(theirs_h)
        try:
            theirs = Medicine.objects.create(name='Theirs', price=Decimal('1'),
                                             expiry_date=_future(), hospital=theirs_h)
        finally:
            clear_current_hospital()

        self.client.post(reverse('medicine_list') + '?missing_cost=1', {
            'bulk_cost': '1', f'cost_{theirs.pk}': '99',
        })
        theirs.refresh_from_db()
        self.assertEqual(theirs.cost_price, Decimal('0.00'))


class ThePastIsEstimatedNeverRewrittenTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')

    def _report(self):
        from reports.utils import module_profit_data
        today = date.today()
        return module_profit_data(today, today)

    def _old_unpriced_sale(self):
        """A sale made before the purchase price existed: cost frozen at 0."""
        med = Medicine.objects.create(name='P', price=Decimal('10'), expiry_date=_future())
        med.add_stock(100, expiry_date=_future())          # no cost known then
        create_sale(cashier=self.admin, items=[{'medicine_id': med.pk, 'quantity': 50}])
        return med

    def test_with_no_purchase_price_known_there_is_nothing_to_estimate(self):
        self._old_unpriced_sale()
        _, totals = self._report()
        self.assertEqual(totals['cost_gap'], Decimal('500.00'))
        self.assertEqual(totals['cost_gap_estimate'], Decimal('0.00'))

    def test_entering_the_price_today_produces_an_estimate_for_the_old_sale(self):
        med = self._old_unpriced_sale()
        med.cost_price = Decimal('8')
        med.save(update_fields=['cost_price'])

        _, totals = self._report()
        self.assertEqual(totals['cost_gap'], Decimal('500.00'))
        self.assertEqual(totals['cost_gap_estimate'], Decimal('400.00'))
        self.assertEqual(totals['profit'], Decimal('500.00'))          # as recorded
        self.assertEqual(totals['estimated_profit'], Decimal('100.00'))

    def test_the_estimate_is_not_written_back_to_the_sale(self):
        med = self._old_unpriced_sale()
        med.cost_price = Decimal('8')
        med.save(update_fields=['cost_price'])
        self._report()

        from sales.models import SaleItem
        self.assertEqual(SaleItem.objects.get().cost_price, Decimal('0.00'),
                         'a frozen sale must never be rewritten from a later guess')

    def test_the_page_labels_it_an_estimate(self):
        med = self._old_unpriced_sale()
        med.cost_price = Decimal('8')
        med.save(update_fields=['cost_price'])

        self.client.force_login(self.admin)
        body = self.client.get(reverse('module_profit_report')).content.decode()
        self.assertIn('estimate', body)
        self.assertIn('nothing here has been changed', body)


class ZeroMarginSalesAreSurfacedTest(TestCase):
    """The other half of the old defect: `add_stock` used to take the SELLING
    price as the cost, so those sales look tracked and report no profit."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')

    def test_they_are_counted_and_named(self):
        med = Medicine.objects.create(name='P', price=Decimal('10'), expiry_date=_future())
        med.add_stock(100, expiry_date=_future(), cost_price=Decimal('10'))   # the old default
        create_sale(cashier=self.admin, items=[{'medicine_id': med.pk, 'quantity': 20}])

        from reports.utils import module_profit_data
        today = date.today()
        _, totals = module_profit_data(today, today)
        self.assertEqual(totals['zero_margin'], Decimal('200.00'))

        self.client.force_login(self.admin)
        body = self.client.get(reverse('module_profit_report')).content.decode()
        self.assertIn('exactly zero margin', body)

    def test_a_normal_sale_is_not_flagged(self):
        med = Medicine.objects.create(name='P', cost_price=Decimal('8'),
                                      price=Decimal('10'), expiry_date=_future())
        med.add_stock(100, expiry_date=_future())
        create_sale(cashier=self.admin, items=[{'medicine_id': med.pk, 'quantity': 20}])

        from reports.utils import module_profit_data
        today = date.today()
        _, totals = module_profit_data(today, today)
        self.assertEqual(totals['zero_margin'], Decimal('0.00'))
