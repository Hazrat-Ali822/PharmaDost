"""Reports: the numbers must be right, and must belong to the viewer's hospital.

Reports aggregate money. Two failure modes matter: a wrong total (the owner makes
decisions on it) and a total that silently includes another tenant's trade.

    python manage.py test reports --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital
from sales.models import Sale
from reports.utils import sales_report_data


def _future():
    return date.today() + timedelta(days=365)


class SalesReportDataTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.u1 = User.objects.create_user(email='u1@t.com', password='pw',
                                          role='ADMIN', hospital=cls.h1)
        cls.u2 = User.objects.create_user(email='u2@t.com', password='pw',
                                          role='ADMIN', hospital=cls.h2)

        # H1: one fully-paid retail bill and one part-paid wholesale bill
        Sale.objects.create(cashier=cls.u1, hospital=cls.h1, sale_type='RETAIL',
                            payment_method='CASH', total=Decimal('1000'),
                            paid=Decimal('1000'))
        Sale.objects.create(cashier=cls.u1, hospital=cls.h1, sale_type='WHOLESALE',
                            payment_method='CREDIT', total=Decimal('4000'),
                            paid=Decimal('1500'))
        # H1: a returned bill, which must be excluded
        Sale.objects.create(cashier=cls.u1, hospital=cls.h1, sale_type='RETAIL',
                            payment_method='CASH', total=Decimal('9999'),
                            paid=Decimal('9999'), is_returned=True)
        # H2: must never appear in H1's report
        Sale.objects.create(cashier=cls.u2, hospital=cls.h2, sale_type='RETAIL',
                            payment_method='CASH', total=Decimal('7777'),
                            paid=Decimal('7777'))

    def tearDown(self):
        clear_current_hospital()

    def _today_range(self):
        today = date.today()
        return today, today

    def test_totals_paid_and_credit(self):
        set_current_hospital(self.h1)
        start, end = self._today_range()
        data = sales_report_data(start, end)
        self.assertEqual(data['total'], Decimal('5000'))    # 1000 + 4000
        self.assertEqual(data['paid'], Decimal('2500'))     # 1000 + 1500
        self.assertEqual(data['credit'], Decimal('2500'))
        self.assertEqual(data['bills'], 2)

    def test_returned_sales_are_excluded(self):
        set_current_hospital(self.h1)
        start, end = self._today_range()
        self.assertNotIn(Decimal('9999'), [sales_report_data(start, end)['total']])

    def test_split_by_sale_type(self):
        set_current_hospital(self.h1)
        start, end = self._today_range()
        data = sales_report_data(start, end)
        self.assertEqual(data['retail']['total'], Decimal('1000'))
        self.assertEqual(data['wholesale']['total'], Decimal('4000'))

    def test_other_tenant_revenue_is_excluded(self):
        set_current_hospital(self.h1)
        start, end = self._today_range()
        self.assertEqual(sales_report_data(start, end)['total'], Decimal('5000'))

        set_current_hospital(self.h2)
        self.assertEqual(sales_report_data(start, end)['total'], Decimal('7777'))

    def test_range_outside_the_sales_returns_zero(self):
        set_current_hospital(self.h1)
        past = date.today() - timedelta(days=30)
        data = sales_report_data(past, past)
        self.assertEqual(data['total'], Decimal('0.00'))
        self.assertEqual(data['bills'], 0)


class ReportViewScopingTest(TestCase):
    """Through the web, the viewer's own hospital is bound by the middleware."""

    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.u1 = User.objects.create_user(email='v1@t.com', password='pw',
                                          role='ADMIN', hospital=cls.h1)
        u2 = User.objects.create_user(email='v2@t.com', password='pw',
                                      role='ADMIN', hospital=cls.h2)
        Sale.objects.create(cashier=cls.u1, hospital=cls.h1, sale_type='RETAIL',
                            payment_method='CASH', total=Decimal('1234'),
                            paid=Decimal('1234'))
        Sale.objects.create(cashier=u2, hospital=cls.h2, sale_type='RETAIL',
                            payment_method='CASH', total=Decimal('8765'),
                            paid=Decimal('8765'))

    def tearDown(self):
        clear_current_hospital()

    def test_sales_report_shows_only_own_revenue(self):
        c = Client(); c.force_login(self.u1)
        resp = c.get(reverse('sales_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['data']['total'], Decimal('1234'))


class ReportAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    def test_doctor_cannot_open_reports(self):
        u = User.objects.create_user(email='doc@t.com', password='pw',
                                     role='DOCTOR', hospital=self.h)
        c = Client(); c.force_login(u)
        self.assertEqual(c.get(reverse('sales_report')).status_code, 403)

    def test_pharmacist_can_open_sales_but_not_profit(self):
        """`profit` is a separate feature from `reports` — margins are not for the counter."""
        u = User.objects.create_user(email='ph@t.com', password='pw',
                                     role='PHARMACIST', hospital=self.h)
        c = Client(); c.force_login(u)
        self.assertEqual(c.get(reverse('sales_report')).status_code, 200)
        self.assertEqual(c.get(reverse('profit_report')).status_code, 403)

    def test_accountant_can_open_profit_and_daybook(self):
        u = User.objects.create_user(email='acc@t.com', password='pw',
                                     role='ACCOUNTANT', hospital=self.h)
        c = Client(); c.force_login(u)
        self.assertEqual(c.get(reverse('profit_report')).status_code, 200)
        self.assertEqual(c.get(reverse('daybook_report')).status_code, 200)
