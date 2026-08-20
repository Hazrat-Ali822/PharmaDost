"""Every module that earns money must be able to show a profit, not a dash.

The owner's ask, and a fair one: an admin has to see what each part of the
hospital actually earns. Imaging, IPD, Emergency and Maternity had no cost
field anywhere in the product, so `Profit by Module` printed their revenue and
then "not recorded" — honest, but useless to the person it is for.

Each now has one, and each in the shape that suits it:

* **Imaging** — a consumable cost on the scan price list, frozen onto the study
  at ordering (a study has no FK to `ScanType`; the name and price are typed).
* **IPD** — the real pharmacy COGS of every ward dose, taken from the batches
  FEFO actually consumed. This was the gap CLAUDE.md had flagged: the medicine
  is billed on the discharge invoice, so its cost belonged to neither pharmacy
  nor IPD and was invisible to both.
* **Emergency / Maternity** — a consumables box beside the charge box that is
  already on those two forms. Optional, because a triage nurse must never be
  blocked by a bookkeeping field.

And the bottom line: gross profit is not what the hospital kept, so operating
expenses are subtracted once at the end.

    python manage.py test tests.test_module_profit_coverage --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.models import Medicine
from patients.models import Patient
from sales.services import create_sale


def _future():
    return date.today() + timedelta(days=365)


def _rows_and_totals():
    from reports.utils import module_profit_data
    today = date.today()
    rows, totals = module_profit_data(today, today)
    return {r['key']: r for r in rows}, totals


class ImagingCostTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.patient = Patient.objects.create(full_name='P', gender='F')

    def _order(self, name, price, modality='XRAY'):
        from imaging.forms import ImagingStudyCreateForm
        from imaging.services import create_study
        form = ImagingStudyCreateForm(data={
            'patient': str(self.patient.pk), 'modality': modality,
            'study_name': name, 'clinical_note': '', 'price': str(price),
        }, user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        return create_study(form, self.admin)

    def test_the_cost_comes_off_the_price_list_and_becomes_a_real_margin(self):
        from imaging.models import ScanType
        ScanType.objects.create(name='Chest X-Ray', modality='XRAY',
                                price=Decimal('800'), cost_price=Decimal('120'))
        study = self._order('Chest X-Ray', 800)
        self.assertEqual(study.cost_price, Decimal('120.00'))

        rows, _ = _rows_and_totals()
        self.assertTrue(rows['IMAGING']['cost_tracked'])
        self.assertEqual(rows['IMAGING']['cost'], Decimal('120.00'))
        self.assertEqual(rows['IMAGING']['profit'], Decimal('680.00'))

    def test_repricing_the_catalogue_cannot_rewrite_a_study_already_done(self):
        from imaging.models import ScanType
        scan = ScanType.objects.create(name='USG', modality='ULTRASOUND',
                                       price=Decimal('1000'), cost_price=Decimal('90'))
        study = self._order('USG', 1000, modality='ULTRASOUND')

        scan.cost_price = Decimal('400')
        scan.save(update_fields=['cost_price'])

        study.refresh_from_db()
        self.assertEqual(study.cost_price, Decimal('90.00'),
                         'the cost is frozen at ordering, same rule as SurgeryRecord')

    def test_a_scan_that_is_not_on_the_list_records_nothing_rather_than_guessing(self):
        self.assertEqual(self._order('Something one-off', 500).cost_price, Decimal('0.00'))

    def test_the_match_ignores_capitals(self):
        from imaging.models import ScanType
        ScanType.objects.create(name='Chest X-Ray', modality='XRAY',
                                price=Decimal('800'), cost_price=Decimal('120'))
        self.assertEqual(self._order('chest x-ray', 800).cost_price, Decimal('120.00'))

    def test_a_cancelled_scan_is_not_counted(self):
        from imaging.models import ScanType
        ScanType.objects.create(name='Chest X-Ray', modality='XRAY',
                                price=Decimal('800'), cost_price=Decimal('120'))
        study = self._order('Chest X-Ray', 800)
        study.status = 'Cancelled'
        study.save(update_fields=['status'])

        rows, _ = _rows_and_totals()
        self.assertEqual(rows.get('IMAGING', {}).get('cost', Decimal('0.00')),
                         Decimal('0.00'))


class WardDoseCostTest(TestCase):
    """The gap CLAUDE.md had flagged: a ward medicine is billed on the IPD
    invoice, so its stock cost belonged to neither pharmacy nor IPD."""

    def setUp(self):
        from ipd.models import Admission, Bed, Ward
        from opd.models import Doctor
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.patient = Patient.objects.create(full_name='P', gender='F')
        doctor = Doctor.objects.create(full_name='Sara Ahmed')
        ward = Ward.objects.create(name='W', daily_rate=Decimal('1000'))
        bed = Bed.objects.create(ward=ward, bed_number='1')
        self.admission = Admission.objects.create(patient=self.patient, bed=bed,
                                                  attending_doctor=doctor)

    def _give(self, med, qty, source='PHARMACY'):
        from ipd.models import MedicationLog
        from ipd.services import log_medication
        log = MedicationLog(admission=self.admission, medicine=med,
                            medicine_name=med.name, dosage='1 vial', quantity=qty,
                            source=source, administered_by=self.admin)
        return log_medication(self.admission, log, medicine=med, user=self.admin)

    def test_the_dose_carries_the_cost_of_the_batch_it_came_from(self):
        med = Medicine.objects.create(name='Inj', cost_price=Decimal('30'),
                                      price=Decimal('50'), expiry_date=_future())
        med.add_stock(20, expiry_date=_future())

        result = self._give(med, 2)
        self.assertIsNone(result.stock_short)
        self.assertEqual(result.log.cost_price, Decimal('30.00'))
        self.assertEqual(result.log.line_cost, Decimal('60.00'))
        self.assertEqual(result.log.charge, Decimal('100.00'))

    def test_it_averages_across_the_batches_fefo_actually_used(self):
        med = Medicine.objects.create(name='Inj', price=Decimal('50'), expiry_date=_future())
        soon = date.today() + timedelta(days=30)
        med.add_stock(2, expiry_date=soon, cost_price=Decimal('10'))
        med.add_stock(8, expiry_date=_future(), cost_price=Decimal('20'))

        result = self._give(med, 4)      # 2 @ 10 + 2 @ 20 = 60 over 4 units
        self.assertEqual(result.log.cost_price, Decimal('15.00'))
        self.assertEqual(result.log.line_cost, Decimal('60.00'))

    def test_the_patients_own_supply_costs_the_hospital_nothing(self):
        med = Medicine.objects.create(name='Inj', cost_price=Decimal('30'),
                                      price=Decimal('50'), expiry_date=_future())
        med.add_stock(20, expiry_date=_future())

        result = self._give(med, 2, source='PATIENT')
        self.assertEqual(result.log.cost_price, Decimal('0.00'))
        self.assertEqual(result.log.charge, Decimal('0.00'))

    def test_a_medicine_with_no_purchase_price_records_zero_not_a_guess(self):
        med = Medicine.objects.create(name='Inj', price=Decimal('50'), expiry_date=_future())
        med.add_stock(20, expiry_date=_future())

        result = self._give(med, 2)
        self.assertEqual(result.log.cost_price, Decimal('0.00'))
        self.assertTrue(result.log.stock_deducted)

    def test_it_reaches_the_report(self):
        med = Medicine.objects.create(name='Inj', cost_price=Decimal('30'),
                                      price=Decimal('50'), expiry_date=_future())
        med.add_stock(20, expiry_date=_future())
        self._give(med, 2)

        rows, _ = _rows_and_totals()
        self.assertTrue(rows['IPD']['cost_tracked'])
        self.assertEqual(rows['IPD']['cost'], Decimal('60.00'))


class CasualtyAndDeliveryConsumablesTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.patient = Patient.objects.create(full_name='P', gender='F')

    def test_both_are_counted(self):
        from emergency.models import EmergencyCase
        from maternity.models import Delivery

        EmergencyCase.objects.create(patient=self.patient, chief_complaint='RTA',
                                     cost_price=Decimal('250'), created_by=self.admin)
        Delivery.objects.create(mother=self.patient, cost_price=Decimal('900'),
                                created_by=self.admin)

        rows, _ = _rows_and_totals()
        self.assertEqual(rows['EMERGENCY']['cost'], Decimal('250.00'))
        self.assertEqual(rows['MATERNITY']['cost'], Decimal('900.00'))
        self.assertTrue(rows['EMERGENCY']['cost_tracked'])
        self.assertTrue(rows['MATERNITY']['cost_tracked'])

    def test_a_blank_box_never_blocks_a_triage_entry(self):
        """A nurse registering a road accident must not be stopped by a
        bookkeeping field."""
        from emergency.forms import EmergencyIntakeForm
        form = EmergencyIntakeForm(data={
            'triage': 'RED', 'chief_complaint': 'RTA', 'mode_of_arrival': 'WALKIN',
            'existing_patient': str(self.patient.pk), 'cost_price': '',
        }, user=self.admin)
        form.is_valid()
        self.assertNotIn('cost_price', form.errors)

    def test_a_blank_box_never_blocks_a_delivery_record(self):
        from maternity.forms import DeliveryForm
        form = DeliveryForm(data={
            'mother': str(self.patient.pk), 'delivery_type': 'NORMAL',
            'outcome': 'LIVE', 'notes': '', 'cost_price': '',
        }, user=self.admin)
        form.is_valid()
        self.assertNotIn('cost_price', form.errors)


class NoEarningModuleIsLeftUnreportedTest(TestCase):
    """The sweep. `OTHER` is the deliberate exception — it is whatever did not
    classify, so there is nothing to attach a cost to."""

    def test_every_module_but_other_can_report_a_cost(self):
        from billing import revenue as rev
        from reports.utils import module_profit_data
        import inspect

        source = inspect.getsource(module_profit_data)
        for key in (rev.OPD, rev.LAB, rev.IMAGING, rev.IPD, rev.OT,
                    rev.EMERGENCY, rev.MATERNITY, rev.AMBULANCE):
            with self.subTest(module=key):
                self.assertIn(f'key == rev.{key}', source,
                              f'{key} falls through to "cost is not recorded" — '
                              f'every earning module needs a cost source')


class NetProfitTest(TestCase):
    """Gross profit is not what the hospital kept."""

    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')

    def test_operating_expenses_are_subtracted_once_at_the_end(self):
        from billing.models import Expense

        med = Medicine.objects.create(name='P', cost_price=Decimal('8'),
                                      price=Decimal('10'), expiry_date=_future())
        med.add_stock(100, expiry_date=_future())
        create_sale(cashier=self.admin, items=[{'medicine_id': med.pk, 'quantity': 50}])
        Expense.objects.create(category='RENT', description='Shop rent',
                               amount=Decimal('60'), recorded_by=self.admin)

        _, totals = _rows_and_totals()
        self.assertEqual(totals['profit'], Decimal('100.00'))     # 500 - 400
        self.assertEqual(totals['expenses'], Decimal('60.00'))
        self.assertEqual(totals['net_profit'], Decimal('40.00'))

    def test_a_loss_is_shown_as_a_loss(self):
        from billing.models import Expense
        Expense.objects.create(category='RENT', description='Shop rent',
                               amount=Decimal('5000'), recorded_by=self.admin)
        _, totals = _rows_and_totals()
        self.assertEqual(totals['net_profit'], Decimal('-5000.00'))

    def test_the_page_shows_the_net_line_and_says_what_the_cost_column_means(self):
        self.client.force_login(self.admin)
        body = self.client.get('/reports/modules/').content.decode()
        self.assertIn('Net profit', body)
        self.assertIn('Operating expenses', body)
        self.assertIn('direct</b> cost', body)
