"""Configurable per-hospital currency symbol."""
from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from user_mgmt.models import SiteSettings, current_currency
from saas.utils import set_current_hospital, clear_current_hospital


class CurrencyTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M', age_years=30,
                                              hospital=self.h)
        self.c = Client(); self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def test_default_is_rs(self):
        set_current_hospital(self.h)
        self.assertEqual(current_currency(), 'Rs')

    def test_changed_symbol_shows_on_the_bill(self):
        set_current_hospital(self.h)
        s = SiteSettings.load(); s.currency_symbol = 'PKR'; s.save()
        self.assertEqual(current_currency(), 'PKR')
        page = self.c.get(f'/billing/patient/{self.patient.pk}/print/')
        self.assertContains(page, 'PKR')
        self.assertNotContains(page, 'Rs ')   # the old hardcoded prefix is gone

    def test_blank_symbol_falls_back_to_rs(self):
        set_current_hospital(self.h)
        s = SiteSettings.load(); s.currency_symbol = ''; s.save()
        self.assertEqual(current_currency(), 'Rs')
