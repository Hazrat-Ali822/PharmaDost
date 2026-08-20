"""Revenue, cost and profit per module — "which part of the hospital earns".

The honesty requirement is the point of this screen. Cost is recorded in exactly
three places: pharmacy (batch COGS frozen on the sale line), lab (a per-test cost
the admin enters on the price list) and OPD (the doctor's own share of the fee).
Nowhere else does a cost exist anywhere in the system, and the report has to say
so — subtracting zero would report those modules at 100% margin, which is a
number an owner would act on.

    python manage.py test tests.test_module_profit --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client

from accounts.models import User
from inventory.models import Medicine, StockBatch
from opd.models import Appointment, Doctor
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital
from sales.services import create_sale


def _future():
    return date.today() + timedelta(days=365)


class ModuleProfitTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='Mixed Hospital', slug='mixed-h',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(email='mx@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.med = Medicine.objects.create(name='Panadol', price=Decimal('50'),
                                           quantity=100, expiry_date=_future(),
                                           hospital=self.h)
        StockBatch.objects.create(medicine=self.med, batch_number='B1',
                                  quantity=100, cost_price=Decimal('30'),
                                  expiry_date=_future(), hospital=self.h)
        self.today = date.today()

    def tearDown(self):
        clear_current_hospital()

    def _data(self):
        from reports.utils import module_profit_data
        return module_profit_data(self.today, self.today)

    def _patient(self, name='A Patient'):
        return Patient.objects.create(full_name=name, gender='M', hospital=self.h)

    def _lab_test(self, price, cost):
        from lab.models import LabTest, TestCategory
        cat = TestCategory.objects.create(name='Haematology', hospital=self.h)
        return LabTest.objects.create(category=cat, name='CBC', price=Decimal(price),
                                      cost_price=Decimal(cost), hospital=self.h)

    # ------------------------------------------------------------------ pharmacy

    def test_pharmacy_profit_is_revenue_minus_the_frozen_batch_cost(self):
        set_current_hospital(self.h)
        try:
            # bought into stock at 30, sold at 50 -> the margin is 20
            create_sale(items=[{'medicine_id': self.med.id, 'quantity': 1,
                                'unit_price': Decimal('50')}],
                        cashier=self.admin, paid=Decimal('50'), customer=None)
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        pharmacy = next(r for r in rows if r['key'] == 'PHARMACY')
        self.assertEqual(pharmacy['revenue'], Decimal('50'))
        self.assertEqual(pharmacy['cost'], Decimal('30'))
        self.assertEqual(pharmacy['profit'], Decimal('20'))
        self.assertTrue(pharmacy['cost_tracked'])

    # ----------------------------------------------------------------------- lab

    def test_lab_profit_uses_the_per_test_cost(self):
        from billing.services import create_service_invoice
        from lab.models import TestOrder, TestResult

        set_current_hospital(self.h)
        try:
            test = self._lab_test('450', '120')
            patient = self._patient('Lab Patient')
            order = TestOrder.objects.create(patient=patient, ordered_by=self.admin)
            TestResult.objects.create(test_order=order, lab_test=test)
            create_service_invoice(patient=patient,
                                   items=[(f'Lab: {test.name}', test.price)],
                                   created_by=self.admin, service='LAB')
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        lab = next(r for r in rows if r['key'] == 'LAB')
        self.assertEqual(lab['revenue'], Decimal('450'))
        self.assertEqual(lab['cost'], Decimal('120'))
        self.assertEqual(lab['profit'], Decimal('330'))

    def test_a_cancelled_test_costs_nothing(self):
        """The reagent was never used, so it must not be charged against profit."""
        from lab.models import TestOrder, TestResult

        set_current_hospital(self.h)
        try:
            test = self._lab_test('450', '120')
            patient = self._patient('Lab Patient')
            order = TestOrder.objects.create(patient=patient, ordered_by=self.admin)
            TestResult.objects.create(test_order=order, lab_test=test,
                                      is_cancelled=True)
            _, totals = self._data()
        finally:
            clear_current_hospital()

        self.assertEqual(totals['cost'], Decimal('0.00'))

    def test_a_lab_with_no_cost_entered_says_so_rather_than_claiming_margin(self):
        from billing.services import create_service_invoice
        from lab.models import TestOrder, TestResult

        set_current_hospital(self.h)
        try:
            test = self._lab_test('450', '0')          # admin never filled it in
            patient = self._patient('Lab Patient')
            order = TestOrder.objects.create(patient=patient, ordered_by=self.admin)
            TestResult.objects.create(test_order=order, lab_test=test)
            create_service_invoice(patient=patient,
                                   items=[(f'Lab: {test.name}', test.price)],
                                   created_by=self.admin, service='LAB')
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        lab = next(r for r in rows if r['key'] == 'LAB')
        self.assertEqual(lab['cost'], Decimal('0.00'))
        self.assertIn('No cost entered', lab['note'])

    # ----------------------------------------------------------------------- OPD

    def test_opd_cost_is_the_doctors_share(self):
        from opd.services import bill_and_notify

        docuser = User.objects.create_user(email='d2@t.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        doctor = Doctor.objects.create(user=docuser, full_name='Sara Ahmed',
                                       opd_fee=Decimal('1000'),
                                       share_percent=Decimal('60'))
        patient = self._patient('OPD Patient')
        set_current_hospital(self.h)
        try:
            appt = Appointment.objects.create(patient=patient, doctor=doctor)
            bill_and_notify(appt, self.admin)
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        opd = next(r for r in rows if r['key'] == 'OPD')
        self.assertEqual(opd['revenue'], Decimal('1000'))
        self.assertEqual(opd['cost'], Decimal('600'))      # 60% goes to the doctor
        self.assertEqual(opd['profit'], Decimal('400'))

    # ------------------------------------------------------- untracked modules

    def test_a_module_with_no_recorded_cost_claims_no_profit(self):
        """`OTHER` is now the only module with no cost source, and deliberately
        so: it holds whatever failed to classify, so there is nothing to attach
        a cost to. Reporting it at 100% margin would be a number the owner acts
        on.

        This test used to be about imaging, which had no cost field anywhere.
        Imaging now has one (`ScanType.cost_price`, frozen onto the study at
        ordering), so the case had to move to a module that is still genuinely
        untracked rather than be deleted — the *behaviour* is what matters, not
        which module happens to lack a cost this month."""
        from billing.services import create_service_invoice

        set_current_hospital(self.h)
        try:
            create_service_invoice(patient=self._patient('Misc Patient'),
                                   items=[('Ambulance standby fee', Decimal('900'))],
                                   created_by=self.admin)
            rows, totals = self._data()
        finally:
            clear_current_hospital()

        other = next(r for r in rows if r['key'] == 'OTHER')
        self.assertEqual(other['revenue'], Decimal('900'))
        self.assertFalse(other['cost_tracked'])
        self.assertEqual(other['cost'], Decimal('0.00'))
        self.assertTrue(totals['partial'],
                        'the screen must warn that total profit is a floor')

    def test_imaging_revenue_with_no_cost_entered_is_still_a_real_row(self):
        """A tenant that has not filled in scan costs yet: the row is tracked
        (there IS a place to record it) and says where to go."""
        from billing.services import create_service_invoice

        set_current_hospital(self.h)
        try:
            create_service_invoice(patient=self._patient('Scan Patient'),
                                   items=[('X-Ray: Chest', Decimal('900'))],
                                   created_by=self.admin, service='IMAGING')
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        imaging = next(r for r in rows if r['key'] == 'IMAGING')
        self.assertEqual(imaging['revenue'], Decimal('900'))
        self.assertEqual(imaging['cost'], Decimal('0.00'))
        self.assertIn('Scan Prices', imaging['note'])

    def test_ipd_ot_and_maternity_are_separate_rows(self):
        """These all collapsed into "Other", which told the owner nothing."""
        from billing.services import create_service_invoice

        set_current_hospital(self.h)
        try:
            create_service_invoice(
                patient=self._patient('Ward Patient'), created_by=self.admin,
                items=[('IPD Bed Charges: Bed 3 (General) — 2 Day(s)', Decimal('4000')),
                       ('OT Surgery: Appendectomy (Surgeon: Dr. X)', Decimal('25000')),
                       ('Delivery — Normal', Decimal('15000'))])
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        by_key = {r['key']: r for r in rows}
        self.assertEqual(by_key['IPD']['revenue'], Decimal('4000'))
        self.assertEqual(by_key['OT']['revenue'], Decimal('25000'))
        self.assertEqual(by_key['MATERNITY']['revenue'], Decimal('15000'))
        self.assertNotIn('OTHER', by_key)

    def test_modules_with_no_activity_are_dropped(self):
        """A pharmacy-only shop must not read a column of zeroes for departments
        it does not have."""
        set_current_hospital(self.h)
        try:
            create_sale(items=[{'medicine_id': self.med.id, 'quantity': 1,
                                'unit_price': Decimal('50')}],
                        cashier=self.admin, paid=Decimal('50'), customer=None)
            rows, _ = self._data()
        finally:
            clear_current_hospital()

        self.assertEqual([r['key'] for r in rows], ['PHARMACY'])

    # --------------------------------------------------------------- the screen

    def test_the_screen_opens_and_is_gated_on_the_profit_feature(self):
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get('/reports/modules/').status_code, 200)

        self.h.enabled_modules = ['pharmacy']        # reports module not bought
        self.h.save(update_fields=['enabled_modules'])
        self.assertEqual(c.get('/reports/modules/').status_code, 403)

    def test_another_tenants_money_never_appears(self):
        other = Hospital.objects.create(name='Other', slug='other-mp',
                                        expiry_date=_future())
        other_admin = User.objects.create_user(email='om@t.com', password='pw',
                                               role='ADMIN', hospital=other)
        other_med = Medicine.objects.create(name='Brufen', price=Decimal('80'),
                                            quantity=50, expiry_date=_future(),
                                            hospital=other)
        StockBatch.objects.create(medicine=other_med, batch_number='X1',
                                  quantity=50, cost_price=Decimal('40'),
                                  expiry_date=_future(), hospital=other)
        set_current_hospital(other)
        try:
            create_sale(items=[{'medicine_id': other_med.id, 'quantity': 5,
                                'unit_price': Decimal('80')}],
                        cashier=other_admin, paid=Decimal('400'), customer=None)
        finally:
            clear_current_hospital()

        set_current_hospital(self.h)
        try:
            create_sale(items=[{'medicine_id': self.med.id, 'quantity': 1,
                                'unit_price': Decimal('50')}],
                        cashier=self.admin, paid=Decimal('50'), customer=None)
            _, totals = self._data()
        finally:
            clear_current_hospital()

        self.assertEqual(totals['revenue'], Decimal('50'))
