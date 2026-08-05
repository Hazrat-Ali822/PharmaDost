"""WhatsApp share on the patient bill (free wa.me link)."""
from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from billing.templatetags.wa import wa_number


class WaNumberTest(TestCase):
    def test_pakistani_formats_normalise_to_international(self):
        self.assertEqual(wa_number("03001234567"), "923001234567")
        self.assertEqual(wa_number("+92 300 1234567"), "923001234567")
        self.assertEqual(wa_number("0092-300-1234567"), "923001234567")
        self.assertEqual(wa_number("3001234567"), "923001234567")
        self.assertEqual(wa_number(""), "")
        self.assertEqual(wa_number(None), "")


class BillWhatsAppButtonTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M', age_years=30,
                                              phone='03001234567', hospital=self.h)
        self.c = Client(); self.c.login(email='a@a.com', password='pw')

    def test_button_shown_with_normalised_number(self):
        page = self.c.get(f'/billing/patient/{self.patient.pk}/')
        self.assertContains(page, 'wa.me/923001234567')
        self.assertContains(page, 'Send on WhatsApp')

    def test_no_button_without_a_phone(self):
        p2 = Patient.objects.create(full_name='NoPhone', gender='M', age_years=30, hospital=self.h)
        page = self.c.get(f'/billing/patient/{p2.pk}/')
        self.assertNotContains(page, 'Send on WhatsApp')
