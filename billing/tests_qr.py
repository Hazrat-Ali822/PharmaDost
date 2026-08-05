"""QR code on the printed patient bill (optional qrcode dependency)."""
from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from user_mgmt.models import SiteSettings
from saas.utils import set_current_hospital, clear_current_hospital


class BillQrTagTest(TestCase):
    def test_tag_returns_data_uri_or_blank(self):
        from billing.templatetags.qr import qr_data_uri
        out = qr_data_uri("hello")
        # qrcode is an optional dependency; either a PNG data URI or '' — never a crash
        self.assertTrue(out == "" or out.startswith("data:image/png;base64,"))
        self.assertEqual(qr_data_uri(""), "")
        self.assertEqual(qr_data_uri(None), "")


class BillQrPrintTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M', age_years=30,
                                              phone='03001234567', hospital=self.h)
        self.c = Client(); self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def test_print_page_renders_and_honours_the_toggle(self):
        # default on — page renders (with a QR if qrcode is installed)
        page = self.c.get(f'/billing/patient/{self.patient.pk}/print/')
        self.assertEqual(page.status_code, 200)

        # turning it off must not break the page and drops the QR block
        set_current_hospital(self.h)
        s = SiteSettings.load(); s.show_bill_qr = False; s.save()
        page = self.c.get(f'/billing/patient/{self.patient.pk}/print/')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Scan for this bill summary')
