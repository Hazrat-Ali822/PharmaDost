"""What a tenant sees when it has bought only *some* of the modules.

The system is sold module by module, and the smallest useful package is
pharmacy-only: a shop with a counter, stock and no patients at all. That
configuration is the one that exposes assumptions the full-hospital case hides,
and it shipped with the dashboard reporting Rs 0 of revenue all day.

    python manage.py test tests.test_module_packaging --settings=pharma_mgmt.test_settings
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


def _stocked(hospital, name='Panadol', price='50'):
    med = Medicine.objects.create(name=name, price=Decimal(price), quantity=100,
                                  expiry_date=_future(), hospital=hospital)
    StockBatch.objects.create(medicine=med, batch_number='B1', quantity=100,
                              cost_price=Decimal('30'), expiry_date=_future(),
                              hospital=hospital)
    return med


class PharmacyOnlyDashboardTest(TestCase):
    """A pharmacy-only tenant's takings must reach the dashboard.

    Every sale over a shop counter has `patient = None`. The dashboard used to
    narrow its pharmacy figure with `.filter(patient__hospital=...)`, an INNER
    JOIN across a nullable FK, which drops exactly those rows — so the
    "Pharmacy Sales" tile read Rs 0 while the "30-Day Revenue" tile immediately
    beside it (no such join) showed the real money on the same screen.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='Pharma Only', slug='pharma-only',
                                         expiry_date=_future(),
                                         enabled_modules=['pharmacy'])
        self.other = Hospital.objects.create(name='Other Shop', slug='other-shop',
                                             expiry_date=_future(),
                                             enabled_modules=['pharmacy'])
        self.admin = User.objects.create_user(email='po@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.med = _stocked(self.h)

    def tearDown(self):
        clear_current_hospital()

    def _walk_in(self, hospital, paid, user):
        """A counter sale: no patient, no customer — the pharmacy's whole day."""
        set_current_hospital(hospital)
        try:
            med = self.med if hospital == self.h else _stocked(hospital, 'Brufen')
            return create_sale(items=[{'medicine_id': med.id, 'quantity': 1,
                                       'unit_price': Decimal(paid)}],
                               cashier=user, paid=Decimal(paid), customer=None)
        finally:
            clear_current_hospital()

    def _dashboard(self):
        c = Client()
        c.force_login(self.admin)
        return c.get('/')

    def test_a_walk_in_sale_reaches_the_pharmacy_revenue_tile(self):
        self._walk_in(self.h, '100', self.admin)
        ctx = self._dashboard().context
        self.assertEqual(ctx['pharmacy_rev'], Decimal('100'))
        self.assertEqual(ctx['total_income_range'], 100.0)

    def test_the_range_and_thirty_day_figures_agree(self):
        """These two sit side by side on one screen. When the range filter covers
        the sale, they must not contradict each other."""
        self._walk_in(self.h, '250', self.admin)
        ctx = self._dashboard().context
        self.assertEqual(ctx['total_income_range'], ctx['total_income_30d'])

    def test_another_tenants_takings_never_appear(self):
        other_admin = User.objects.create_user(email='os@t.com', password='pw',
                                               role='ADMIN', hospital=self.other)
        self._walk_in(self.h, '100', self.admin)
        self._walk_in(self.other, '9999', other_admin)
        ctx = self._dashboard().context
        self.assertEqual(ctx['pharmacy_rev'], Decimal('100'))

    def test_a_hospital_less_non_superuser_sees_nothing(self):
        """Fail-closed: dropping the patient join must not drop the isolation."""
        self._walk_in(self.h, '100', self.admin)
        stray = User.objects.create_user(email='stray@t.com', password='pw',
                                         role='ADMIN', hospital=None)
        c = Client()
        c.force_login(stray)
        resp = c.get('/')
        if resp.status_code == 200:
            self.assertEqual(resp.context['pharmacy_rev'], Decimal('0.00'))

    def test_a_returned_sale_is_not_counted(self):
        sale = self._walk_in(self.h, '100', self.admin)
        sale.is_returned = True
        sale.save(update_fields=['is_returned'])
        self.assertEqual(self._dashboard().context['pharmacy_rev'], Decimal('0.00'))


class OpdRevenueClassificationTest(TestCase):
    """OPD consultation money must land in the OPD tile, not in "Other".

    Two code paths write the invoice line and they disagree on its wording:
    `billing.services.create_opd_invoice` writes 'OPD Consultation', while
    `opd.services.bill_and_notify` — the path reception, the booking form and
    offline replay all take — writes 'OPD Consultation — Dr. Name'. The
    dashboard tested for equality, so the second (and far more common) form was
    never recognised.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='Full Hospital', slug='full-h',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(email='fh@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        docuser = User.objects.create_user(email='doc@t.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=docuser, full_name='Sara Ahmed',
                                            opd_fee=Decimal('800'))
        self.patient = Patient.objects.create(full_name='Ali Khan', gender='M',
                                              hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def test_a_booked_consultation_counts_as_opd_not_other(self):
        """The tiles are cash-basis — each line is scaled by the invoice's
        paid/total — so the fee has to be collected before it shows anywhere.
        What is under test is *which* tile it lands in."""
        from billing.models import Invoice
        from opd.services import bill_and_notify
        set_current_hospital(self.h)
        try:
            appt = Appointment.objects.create(patient=self.patient, doctor=self.doctor)
            bill_and_notify(appt, self.admin)
            invoice = Invoice.objects.get()
            invoice.paid = invoice.total          # reception takes the fee
            invoice.save(update_fields=['paid'])
        finally:
            clear_current_hospital()

        c = Client()
        c.force_login(self.admin)
        ctx = c.get('/').context
        self.assertEqual(ctx['opd_rev'], Decimal('800'))
        self.assertEqual(ctx['other_rev'], Decimal('0.00'))

    def test_an_uncollected_consultation_is_not_counted_as_income(self):
        from opd.services import bill_and_notify
        set_current_hospital(self.h)
        try:
            appt = Appointment.objects.create(patient=self.patient, doctor=self.doctor)
            bill_and_notify(appt, self.admin)
        finally:
            clear_current_hospital()

        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get('/').context['opd_rev'], Decimal('0.00'))


class RevenueClassificationTest(TestCase):
    """Invoice lines are read back as departments by parsing their description.

    Two screens did that with two different rules and got two different answers,
    so both now go through `billing.revenue.classify`.
    """

    def test_every_modality_is_recognised_as_imaging(self):
        """Derived from the model's own choices, so a new modality cannot start
        quietly filing its money under "Other"."""
        from billing import revenue
        from imaging.models import ImagingStudy
        for code, label in ImagingStudy.MODALITY_CHOICES:
            if code == 'OTHER':
                continue
            self.assertEqual(revenue.classify(f'{label}: Chest'), revenue.IMAGING,
                             f'{label} not classified as imaging')

    def test_injection_is_not_a_ct_scan(self):
        """The analytics chart matched `icontains='CT'`, and 'Injection' contains
        'ct' — a ward injection charge was counted as radiology revenue."""
        from billing import revenue
        for desc in ('Injection: Diclofenac', 'Extraction', 'Doctor visit fee'):
            self.assertEqual(revenue.classify(desc), revenue.OTHER, desc)

    def test_both_opd_wordings_are_recognised(self):
        from billing import revenue
        self.assertEqual(revenue.classify('OPD Consultation'), revenue.OPD)
        self.assertEqual(revenue.classify('OPD Consultation — Dr. Sara Ahmed'),
                         revenue.OPD)


class AnalyticsReturnedSaleTest(TestCase):
    """A refunded sale keeps its `total`; the analytics chart counted it."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Analytics Shop', slug='an-shop',
                                         expiry_date=_future(),
                                         enabled_modules=['pharmacy', 'reports'])
        self.admin = User.objects.create_user(email='an@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.med = _stocked(self.h)

    def tearDown(self):
        clear_current_hospital()

    def test_a_returned_sale_is_not_charted_as_revenue(self):
        set_current_hospital(self.h)
        try:
            sale = create_sale(items=[{'medicine_id': self.med.id, 'quantity': 1,
                                       'unit_price': Decimal('100')}],
                               cashier=self.admin, paid=Decimal('100'), customer=None)
            sale.is_returned = True
            sale.save(update_fields=['is_returned'])
        finally:
            clear_current_hospital()

        c = Client()
        c.force_login(self.admin)
        resp = c.get('/reports/analytics/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['pharmacy_rev'], Decimal('0.00'))
        self.assertEqual(resp.context['total_rev'], Decimal('0.00'))


class PharmacyOnlyNavTest(TestCase):
    """The sidebar and the URL space must agree with the modules bought."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Pharma Only', slug='pharma-only-2',
                                         expiry_date=_future(),
                                         enabled_modules=['pharmacy'])
        self.admin = User.objects.create_user(email='pn@t.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.client_ = Client()
        self.client_.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def test_no_empty_price_list_heading(self):
        """`catalog` is a CORE feature, so it is on even here — but both links
        under that heading need lab/imaging, leaving a title over nothing."""
        html = self.client_.get('/').content.decode()
        self.assertNotIn('Price List', html)

    def test_the_lab_and_scan_price_editors_are_closed(self):
        for url in ('/lab/tests/', '/imaging/scans/'):
            self.assertEqual(self.client_.get(url).status_code, 403, url)

    def test_pharmacy_alone_cannot_report_on_itself(self):
        """Documents *why* `reports` is part of the recommended pharmacy package:
        on its own, a shop cannot see its own sales, profit or stock valuation."""
        for url in ('/reports/sales/', '/reports/profit/', '/reports/inventory/',
                    '/reports/analytics/', '/reports/daybook/'):
            self.assertEqual(self.client_.get(url).status_code, 403, url)

    def test_the_recommended_pharmacy_package_opens_every_screen_it_should(self):
        self.h.enabled_modules = ['pharmacy', 'reports', 'finance']
        self.h.save(update_fields=['enabled_modules'])
        for url in ('/', '/sales/new/', '/sales/list/', '/medicines/',
                    '/medicines/purchase-orders/', '/suppliers/', '/customers/',
                    '/reports/sales/', '/reports/profit/', '/reports/inventory/',
                    '/reports/analytics/', '/reports/daybook/',
                    '/billing/expenses/', '/billing/cash-closing/'):
            self.assertEqual(self.client_.get(url).status_code, 200, url)

    def test_they_open_again_once_those_modules_are_bought(self):
        """The guard must gate on the module, not simply block the screen."""
        self.h.enabled_modules = ['pharmacy', 'lab', 'imaging']
        self.h.save(update_fields=['enabled_modules'])
        for url in ('/lab/tests/', '/imaging/scans/'):
            self.assertEqual(self.client_.get(url).status_code, 200, url)
        self.assertIn('Price List', self.client_.get('/').content.decode())
