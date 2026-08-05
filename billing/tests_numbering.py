"""Per-hospital invoice numbering (INV-YYYY-00001)."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from user_mgmt.models import SiteSettings
from saas.utils import set_current_hospital, clear_current_hospital
from billing.services import create_service_invoice


class InvoiceNumberingTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Alpha', slug='alpha',
                                         expiry_date=date.today() + timedelta(days=365))
        self.user = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M',
                                              age_years=30, hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _invoice(self):
        return create_service_invoice(patient=self.patient,
                                      items=[('Test', Decimal('100'))],
                                      created_by=self.user)

    def test_new_invoices_get_a_sequential_yearly_number(self):
        set_current_hospital(self.h)
        year = timezone.localdate().year
        a = self._invoice()
        b = self._invoice()
        self.assertEqual(a.number, f'INV-{year}-00001')
        self.assertEqual(b.number, f'INV-{year}-00002')
        self.assertEqual(a.display_no, f'INV-{year}-00001')

    def test_each_hospital_numbers_independently(self):
        h2 = Hospital.objects.create(name='Beta', slug='beta',
                                     expiry_date=date.today() + timedelta(days=365))
        p2 = Patient.objects.create(full_name='Sara', gender='F', age_years=25, hospital=h2)
        u2 = User.objects.create_user(email='b@b.com', password='pw', role='ADMIN', hospital=h2)
        year = timezone.localdate().year

        set_current_hospital(self.h)
        a = self._invoice()
        set_current_hospital(h2)
        b = create_service_invoice(patient=p2, items=[('X', Decimal('50'))], created_by=u2)

        self.assertEqual(a.number, f'INV-{year}-00001')
        self.assertEqual(b.number, f'INV-{year}-00001')   # its own counter, no clash

    def test_year_switched_off_gives_a_plain_number(self):
        set_current_hospital(self.h)
        s = SiteSettings.load()
        s.invoice_year_in_number = False
        s.invoice_prefix = 'BILL'
        s.save()
        a = self._invoice()
        self.assertEqual(a.number, 'BILL-00001')

    def test_display_no_falls_back_to_id_for_unnumbered_rows(self):
        set_current_hospital(self.h)
        inv = self._invoice()
        # simulate a legacy row that predates numbering
        type(inv).objects.filter(pk=inv.pk).update(number=None)
        inv.refresh_from_db()
        self.assertEqual(inv.display_no, f'#{inv.pk}')
