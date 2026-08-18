"""The money-and-stock defects a browser QA pass found on 18 August 2026.

Five of them, and they share a shape: each one lets the screen say something the
database does not agree with. That is the expensive kind of bug in a hospital,
because nobody catches it on the day — it turns up as a shortfall in the drawer,
or a stock count that will not reconcile, and by then it cannot be traced.

    python manage.py test tests.test_qa_20260818 --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase

from accounts.models import User
from inventory.models import Medicine, StockBatch
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital
from sales.services import create_sale


def _future():
    return date.today() + timedelta(days=365)


class QABase(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='QA Hospital', slug='qa-h',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(email='qa@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        set_current_hospital(self.h)
        self.patient = Patient.objects.create(full_name='Ayesha Bibi', hospital=self.h)
        self.c = Client()
        self.c.login(email='qa@t.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def _med(self, name='Panadol', price='50', qty=100):
        med = Medicine.objects.create(name=name, price=Decimal(price), quantity=qty,
                                      expiry_date=_future(), hospital=self.h)
        StockBatch.objects.create(medicine=med, batch_number='B1', quantity=qty,
                                  cost_price=Decimal('30'), expiry_date=_future(),
                                  hospital=self.h)
        return med


class UnpricedMedicineTest(QABase):
    """#6 — ~200 medicines sit in the catalogue at Rs 0.00 and ring up free.

    "Load Standard Catalog" files names for an admin to price later. Until they
    do, every one of them sells for nothing and lands in the profit report as
    pure margin, which is worse than not selling at all: the numbers look fine.
    """

    def test_a_medicine_with_no_price_cannot_be_sold(self):
        med = self._med('Actifed', price='0')
        with self.assertRaises(ValueError) as caught:
            create_sale(items=[{'medicine_id': med.id, 'quantity': 2}],
                        cashier=self.admin)
        self.assertIn('no price set', str(caught.exception))

    def test_the_refusal_names_the_medicine_and_what_to_do(self):
        med = self._med('Actifed', price='0')
        with self.assertRaises(ValueError) as caught:
            create_sale(items=[{'medicine_id': med.id, 'quantity': 1}],
                        cashier=self.admin)
        message = str(caught.exception)
        self.assertIn('Actifed', message)
        self.assertIn('Inventory', message)

    def test_an_explicit_price_is_still_honoured(self):
        """A hospital does sometimes issue something free, and the cashier
        typing a price is a decision rather than an omission."""
        med = self._med('Actifed', price='0')
        sale = create_sale(items=[{'medicine_id': med.id, 'quantity': 1,
                                   'unit_price': '0'}], cashier=self.admin)
        self.assertEqual(sale.items.count(), 1)

    def test_a_priced_medicine_is_unaffected(self):
        med = self._med('Panadol', price='50')
        sale = create_sale(items=[{'medicine_id': med.id, 'quantity': 2}],
                           cashier=self.admin)
        self.assertEqual(sale.total, Decimal('100.00'))

    def test_the_inventory_list_says_which_rows_have_no_price(self):
        self._med('Actifed', price='0')
        html = self.c.get('/medicines/').content.decode()
        self.assertIn('No price set', html)


class WardMedicineTruthTest(QABase):
    """#4 — stock came off the shelf and the chart said it did not.

    The wording keyed on `unit_price > 0`, which answers "was this billed",
    not "was this issued". An unpriced catalogue medicine was deducted from
    inventory while the chart printed "no stock movement or charge" under it —
    the drug chart contradicting the stock ledger.
    """

    def _admission(self):
        from ipd.models import Admission, Bed, Ward
        ward = Ward.objects.create(name='General', daily_rate=Decimal('1000'),
                                   hospital=self.h)
        bed = Bed.objects.create(ward=ward, bed_number='B1', hospital=self.h)
        from opd.models import Doctor
        doctor = Doctor.objects.create(full_name='Sara Ahmed', user=self.admin)
        return Admission.objects.create(patient=self.patient, bed=bed,
                                        attending_doctor=doctor, hospital=self.h)

    def _give(self, med, qty=4):
        from ipd.models import MedicationLog
        from ipd.services import log_medication
        log = MedicationLog(medicine_name=med.name, dosage='1 tab',
                            quantity=qty, source='PHARMACY', hospital=self.h)
        return log_medication(self._admission(), log, med, self.admin).log

    def test_an_unpriced_dose_still_records_that_stock_moved(self):
        med = self._med('Zyrtec', price='0', qty=10)
        log = self._give(med, 4)
        med.refresh_from_db()
        self.assertEqual(med.quantity, 6)          # it did come off the shelf
        self.assertTrue(log.stock_deducted)        # and the record says so

    def test_the_chart_wording_matches_what_happened(self):
        med = self._med('Zyrtec', price='0', qty=10)
        log = self._give(med, 4)
        self.assertIn('Issued from pharmacy stock', log.issue_note)
        self.assertIn('no price set', log.issue_note)
        self.assertNotIn('Not issued', log.issue_note)

    def test_a_priced_dose_still_says_it_was_billed(self):
        med = self._med('Amoxil', price='135', qty=10)
        log = self._give(med, 4)
        self.assertTrue(log.stock_deducted)
        self.assertIn('Billed', log.issue_note)
        self.assertEqual(log.charge, Decimal('540.00'))

    def test_the_patients_own_supply_moves_no_stock_and_says_so(self):
        from ipd.models import MedicationLog
        from ipd.services import log_medication
        med = self._med('Brufen', price='40', qty=10)
        log = MedicationLog(medicine_name='Brufen', dosage='1 tab', quantity=2,
                            source='PATIENT', hospital=self.h)
        result = log_medication(self._admission(), log, med, self.admin)
        med.refresh_from_db()
        self.assertEqual(med.quantity, 10)
        self.assertFalse(result.log.stock_deducted)
        self.assertIn('Not issued by the pharmacy', result.log.issue_note)


class PaymentCannotExceedOutstandingTest(QABase):
    """#2 — the till accepted Rs 999,999 against a Rs 8,390 bill.

    Allocation stops at the balance, so the excess was written to the payment
    ledger and allocated nowhere: the patient's history showed the full amount
    while the day book showed the real cash, and nothing on either screen
    explained the gap.
    """

    def _invoice(self, total='1000'):
        from billing.models import Invoice, InvoiceItem
        inv = Invoice.objects.create(patient=self.patient, subtotal=Decimal(total),
                                     total=Decimal(total), hospital=self.h,
                                     created_by=self.admin)
        InvoiceItem.objects.create(invoice=inv, description='Lab: CBC',
                                   amount=Decimal(total))
        return inv

    def test_an_overpayment_is_refused(self):
        self._invoice('1000')
        from billing.models import PatientPayment
        r = self.c.post(f'/billing/patient/{self.patient.pk}/',
                        {'amount': '999999', 'payment_method': 'CASH'}, follow=True)
        self.assertEqual(PatientPayment.objects.count(), 0)
        self.assertIn('more than', r.content.decode())

    def test_the_refusal_names_the_amount_actually_owed(self):
        self._invoice('1000')
        r = self.c.post(f'/billing/patient/{self.patient.pk}/',
                        {'amount': '5000', 'payment_method': 'CASH'}, follow=True)
        self.assertIn('1000', r.content.decode())

    def test_paying_exactly_the_balance_works(self):
        self._invoice('1000')
        from billing.models import PatientPayment
        self.c.post(f'/billing/patient/{self.patient.pk}/',
                    {'amount': '1000', 'payment_method': 'CASH'}, follow=True)
        self.assertEqual(PatientPayment.objects.count(), 1)

    def test_a_part_payment_works(self):
        self._invoice('1000')
        from billing.models import PatientPayment
        self.c.post(f'/billing/patient/{self.patient.pk}/',
                    {'amount': '400', 'payment_method': 'CASH'}, follow=True)
        self.assertEqual(PatientPayment.objects.get().amount, Decimal('400.00'))

    def test_zero_is_still_refused(self):
        self._invoice('1000')
        from billing.models import PatientPayment
        self.c.post(f'/billing/patient/{self.patient.pk}/',
                    {'amount': '0', 'payment_method': 'CASH'}, follow=True)
        self.assertEqual(PatientPayment.objects.count(), 0)


class PosKeepsTheCartOnErrorTest(QABase):
    """#3 — one line over stock and the whole basket was thrown away.

    Every line, quantity, price, line discount, order discount and the walk-in
    name were lost, leaving one empty row. At a counter with a queue that is the
    difference between a thirty-second sale and starting again.
    """

    def test_the_basket_survives_a_stock_error(self):
        a = self._med('Panadol', price='30', qty=100)
        b = self._med('Brufen', price='40', qty=2)
        r = self.c.post('/sales/new/', {
            'sale_type': 'RETAIL', 'payment_method': 'CASH',
            'customer_name': 'Walk-in Karim', 'discount': '50',
            'medicine_id[]': [str(a.id), str(b.id)],
            'quantity[]': ['3', '9999'],
            'unit_price[]': ['', ''],
            'line_discount[]': ['5', '0'],
        })
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Not enough', html)               # the error is shown
        self.assertIn(f'"medicine_id": {a.id}', html)   # ...and so is the cart
        self.assertIn('"quantity": 3', html)
        self.assertIn('Walk-in Karim', html)
        self.assertIn('value="50"', html)               # the order discount

    def test_a_good_sale_is_unaffected(self):
        a = self._med('Panadol', price='30', qty=100)
        r = self.c.post('/sales/new/', {
            'sale_type': 'RETAIL', 'payment_method': 'CASH',
            'medicine_id[]': [str(a.id)], 'quantity[]': ['2'],
            'unit_price[]': [''], 'line_discount[]': ['0'],
        })
        self.assertEqual(r.status_code, 302)            # straight to the receipt


class DashboardTilesSumToTheTotalTest(QABase):
    """#1 — "Total Income" was not the sum of the tiles beside it.

    Ward, theatre, emergency and maternity revenue all fell into a category with
    no tile at all, so a day with an inpatient bill visibly failed to add up.
    Separately, each tile was gated on `nav.<module>` while the total was not.
    """

    def _paid_invoice(self, description, amount):
        from billing.models import Invoice, InvoiceItem
        inv = Invoice.objects.create(patient=self.patient, subtotal=Decimal(amount),
                                     total=Decimal(amount), paid=Decimal(amount),
                                     hospital=self.h, created_by=self.admin)
        InvoiceItem.objects.create(invoice=inv, description=description,
                                   amount=Decimal(amount))

    def test_ipd_revenue_gets_a_tile_of_its_own(self):
        self._paid_invoice('IPD Bed Charges: General', '5540')
        r = self.c.get('/', follow=True)
        cards = r.context['dept_cards']
        self.assertIn('IPD', [c['key'] for c in cards])

    def test_the_tiles_add_up_to_the_total(self):
        """The invariant, stated in CLAUDE.md and broken twice."""
        self._paid_invoice('OPD Consultation — Dr. Sara Ahmed', '1500')
        self._paid_invoice('Lab: CBC', '850')
        self._paid_invoice('IPD Bed Charges: General', '5540')
        self._paid_invoice('CT: Brain', '2500')

        r = self.c.get('/', follow=True)
        cards = r.context['dept_cards']
        self.assertAlmostEqual(float(sum(c['amount'] for c in cards)),
                               r.context['total_income_range'], places=2)

    def test_a_category_that_earned_nothing_gets_no_tile(self):
        self._paid_invoice('Lab: CBC', '850')
        keys = [c['key'] for c in self.c.get('/', follow=True).context['dept_cards']]
        self.assertIn('LAB', keys)
        self.assertNotIn('MATERNITY', keys)

    def test_an_unknown_description_still_reaches_a_tile(self):
        """Anything the classifier cannot place must land in Other rather than
        vanish from the tiles while staying in the total."""
        self._paid_invoice('Something nobody has classified', '700')
        keys = [c['key'] for c in self.c.get('/', follow=True).context['dept_cards']]
        self.assertIn('OTHER', keys)
