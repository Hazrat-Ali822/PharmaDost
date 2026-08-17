"""Brute-force protection and report exports.

    python manage.py test tests.test_hardening --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from audit.models import AuditLog
from inventory.models import Medicine, StockBatch
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _future():
    return date.today() + timedelta(days=365)


@override_settings(LOCKOUT_THRESHOLD=3, LOCKOUT_WINDOW_MINUTES=15,
                   LOCKOUT_MINUTES=15)
class LoginLockoutTest(TestCase):
    """Guessing a password had no consequence: the audit log noticed a burst and
    told the admin, but nothing refused the next attempt."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-lock',
                                         expiry_date=_future())
        self.user = User.objects.create_user(email='staff@lock.com', password='rightpw',
                                             role='ADMIN', hospital=self.h)
        self.client_ = Client()

    def tearDown(self):
        clear_current_hospital()

    def _attempt(self, password, email='staff@lock.com'):
        return self.client_.post('/accounts/login/',
                                 {'username': email, 'password': password})

    def test_repeated_wrong_passwords_end_in_a_lockout(self):
        for _ in range(3):
            self._attempt('wrong')
        self.assertEqual(self._attempt('wrong').status_code, 429)

    def test_the_lockout_holds_even_against_the_correct_password(self):
        """Otherwise it is no protection at all — the attacker's next guess is
        the one that works."""
        for _ in range(3):
            self._attempt('wrong')
        resp = self._attempt('rightpw')
        self.assertEqual(resp.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client_.session)

    def test_a_few_mistakes_do_not_lock_anyone_out(self):
        self._attempt('wrong')
        self._attempt('wrong')
        resp = self._attempt('rightpw')
        self.assertNotEqual(resp.status_code, 429)

    def test_a_different_account_from_the_same_place_is_unaffected(self):
        other = User.objects.create_user(email='other@lock.com', password='pw2',
                                         role='ADMIN', hospital=self.h)
        for _ in range(4):
            self._attempt('wrong')
        resp = self._attempt('pw2', email='other@lock.com')
        self.assertNotEqual(resp.status_code, 429)

    def test_the_lockout_expires(self):
        for _ in range(4):
            self._attempt('wrong')
        self.assertEqual(self._attempt('wrong').status_code, 429)
        # Age the recorded failures past the window.
        AuditLog.all_objects.filter(action='LOGIN_FAILED').update(
            timestamp=timezone.now() - timedelta(days=1))
        self.assertNotEqual(self._attempt('rightpw').status_code, 429)

    def test_failures_are_recorded_with_where_they_came_from(self):
        """A security log that cannot say *where from* is half a log, and the
        lockout counts per (email, IP) so one attacker cannot shut a whole
        hospital's staff out of their own system."""
        self.client_.post('/accounts/login/',
                          {'username': 'staff@lock.com', 'password': 'wrong'},
                          REMOTE_ADDR='203.0.113.9')
        row = AuditLog.all_objects.filter(action='LOGIN_FAILED').first()
        self.assertEqual(row.ip_address, '203.0.113.9')

    def test_a_lockout_from_one_address_does_not_stop_another(self):
        for _ in range(4):
            self.client_.post('/accounts/login/',
                              {'username': 'staff@lock.com', 'password': 'wrong'},
                              REMOTE_ADDR='203.0.113.9')
        resp = Client().post('/accounts/login/',
                             {'username': 'staff@lock.com', 'password': 'rightpw'},
                             REMOTE_ADDR='198.51.100.4')
        self.assertNotEqual(resp.status_code, 429)


class ReportExportTest(TestCase):
    """Every report was screen-only; an accountant works in Excel."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-csv',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(email='a@csv.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.c = Client()
        self.c.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def test_every_report_exports(self):
        for name in ('sales_report', 'profit_report', 'daybook_report',
                     'inventory_report', 'module_profit_report'):
            resp = self.c.get(reverse(name), {'export': 'csv'})
            self.assertEqual(resp.status_code, 200, name)
            self.assertIn('text/csv', resp['Content-Type'], name)
            self.assertIn('attachment', resp['Content-Disposition'], name)

    def test_the_inventory_export_carries_the_rows(self):
        med = Medicine.objects.create(name='Panadol', price=Decimal('50'),
                                      quantity=10, expiry_date=_future(),
                                      hospital=self.h)
        StockBatch.objects.create(medicine=med, batch_number='B1', quantity=10,
                                  cost_price=Decimal('30'), expiry_date=_future(),
                                  hospital=self.h)
        body = self.c.get(reverse('inventory_report'),
                          {'export': 'csv'}).content.decode('utf-8-sig')
        self.assertIn('Panadol', body)
        self.assertIn('Medicine,Generic', body)

    def test_a_formula_in_a_name_is_defused(self):
        """A medicine named "=cmd|..." would otherwise be executed by Excel when
        the accountant opens the file."""
        Medicine.objects.create(name='=cmd|calc', price=Decimal('1'), quantity=1,
                                expiry_date=_future(), hospital=self.h)
        body = self.c.get(reverse('inventory_report'),
                          {'export': 'csv'}).content.decode('utf-8-sig')
        self.assertIn("'=cmd|calc", body)

    def test_the_export_is_tenant_scoped(self):
        other = Hospital.objects.create(name='Other', slug='o-csv',
                                        expiry_date=_future())
        Medicine.objects.create(name='THEIRSECRETMED', price=Decimal('1'),
                                quantity=1, expiry_date=_future(), hospital=other)
        body = self.c.get(reverse('inventory_report'),
                          {'export': 'csv'}).content.decode('utf-8-sig')
        self.assertNotIn('THEIRSECRETMED', body)
