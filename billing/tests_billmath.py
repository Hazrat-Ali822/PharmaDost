"""Tax %, standing discount % and rounding on bills (all opt-in, default no-op)."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from inventory.models import Medicine
from user_mgmt.models import SiteSettings
from saas.utils import set_current_hospital, clear_current_hospital
from sales.services import create_sale
from sales.models import Sale
from billing.services import create_service_invoice


class BillMathTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Alpha', slug='alpha',
                                         expiry_date=date.today() + timedelta(days=365))
        self.user = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M',
                                              age_years=30, hospital=self.h)
        set_current_hospital(self.h)
        self.med = Medicine.objects.create(name='Panadol', price=Decimal('100'),
                                           quantity=100, hospital=self.h,
                                           expiry_date=date.today() + timedelta(days=365))

    def tearDown(self):
        clear_current_hospital()

    def _settings(self, **kw):
        s = SiteSettings.load()
        for k, v in kw.items():
            setattr(s, k, v)
        s.save()
        return s

    def _sale(self, qty=1, **kw):
        return create_sale(items=[{'medicine_id': self.med.id, 'quantity': qty}],
                           cashier=self.user, **kw)

    def test_defaults_are_a_no_op(self):
        sale = self._sale()
        self.assertEqual(sale.total, Decimal('100.00'))
        self.assertEqual(sale.tax, Decimal('0.00'))
        self.assertEqual(sale.discount, Decimal('0.00'))

    def test_tax_is_added_to_the_sale(self):
        self._settings(default_tax_percent=Decimal('10'))
        sale = self._sale()          # 100 + 10% = 110
        self.assertEqual(sale.tax, Decimal('10.00'))
        self.assertEqual(sale.total, Decimal('110.00'))

    def test_standing_discount_applies_when_box_is_blank(self):
        self._settings(default_discount_percent=Decimal('10'))
        sale = self._sale()          # discount=None -> 10% off 100 = 90
        self.assertEqual(sale.discount, Decimal('10.00'))
        self.assertEqual(sale.total, Decimal('90.00'))

    def test_explicit_discount_overrides_the_standing_default(self):
        self._settings(default_discount_percent=Decimal('10'))
        sale = self._sale(discount=0)   # explicit 0 wins
        self.assertEqual(sale.discount, Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('100.00'))

    def test_rounding_to_nearest_5(self):
        self._settings(default_tax_percent=Decimal('3'), bill_rounding='5')
        sale = self._sale()          # 100 + 3% = 103 -> nearest 5 = 105
        self.assertEqual(sale.total, Decimal('105'))

    def test_tax_and_rounding_on_a_service_invoice(self):
        self._settings(default_tax_percent=Decimal('10'), bill_rounding='none')
        inv = create_service_invoice(patient=self.patient,
                                     items=[('X-ray', Decimal('500'))],
                                     created_by=self.user)
        self.assertEqual(inv.tax, Decimal('50.00'))
        self.assertEqual(inv.total, Decimal('550.00'))
