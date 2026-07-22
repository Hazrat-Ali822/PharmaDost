"""Tests for the tenancy engine itself — the layer every other app depends on.

Covers `TenantManager`'s three resolution cases, the global `auto_assign_hospital`
pre_save signal, the subscription gate, and hospital provisioning.

    python manage.py test saas --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import (
    set_current_hospital, set_tenant_strict, clear_current_hospital,
    get_current_hospital,
)


def _future():
    return date.today() + timedelta(days=365)


class TenantManagerTest(TestCase):
    """The manager has three modes; each one matters and they are easy to conflate."""

    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.p1 = Patient.objects.create(full_name='P1', gender='M', mrn='H1-1', hospital=cls.h1)
        cls.p2 = Patient.objects.create(full_name='P2', gender='M', mrn='H2-1', hospital=cls.h2)
        cls.orphan = Patient.objects.create(full_name='Orphan', gender='M', mrn='NO-HOSP',
                                            hospital=None)

    def tearDown(self):
        clear_current_hospital()

    def test_bound_hospital_filters_to_it(self):
        set_current_hospital(self.h1)
        names = set(Patient.objects.values_list('full_name', flat=True))
        self.assertEqual(names, {'P1'})

    def test_strict_without_hospital_sees_only_hospital_less_rows(self):
        """A logged-in user with no hospital must NOT see other tenants' rows."""
        set_current_hospital(None)
        set_tenant_strict(True)
        names = set(Patient.objects.values_list('full_name', flat=True))
        self.assertEqual(names, {'Orphan'})

    def test_unstrict_without_hospital_sees_everything(self):
        """Management commands and cron need the full cross-tenant view."""
        set_current_hospital(None)
        set_tenant_strict(False)
        names = set(Patient.objects.values_list('full_name', flat=True))
        self.assertEqual(names, {'P1', 'P2', 'Orphan'})

    def test_clear_resets_both_hospital_and_strict(self):
        set_current_hospital(self.h1)
        set_tenant_strict(True)
        clear_current_hospital()
        self.assertIsNone(get_current_hospital())
        self.assertEqual(Patient.objects.count(), 3)   # back to unfiltered


class AutoAssignHospitalSignalTest(TestCase):
    """`saas.signals.auto_assign_hospital` stamps the bound hospital on save."""

    def tearDown(self):
        clear_current_hospital()

    def test_hospital_is_stamped_from_thread_local(self):
        h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(h)
        p = Patient.objects.create(full_name='Auto', gender='M', mrn='AUTO-1')
        self.assertEqual(p.hospital, h)

    def test_explicit_hospital_is_not_overwritten(self):
        h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        set_current_hospital(h1)
        p = Patient.objects.create(full_name='Explicit', gender='M', mrn='EXP-1', hospital=h2)
        self.assertEqual(p.hospital, h2)

    def test_no_hospital_bound_leaves_it_null(self):
        set_current_hospital(None)
        p = Patient.objects.create(full_name='Null', gender='M', mrn='NULL-1')
        self.assertIsNone(p.hospital)


class SubscriptionGateTest(TestCase):
    """An unpaid or suspended tenant is locked out of the app, not silently served."""

    def _login_admin_of(self, hospital, email):
        user = User.objects.create_user(email=email, password='pw', role='ADMIN',
                                        hospital=hospital)
        c = Client()
        c.force_login(user)
        return c

    def test_expired_subscription_shows_suspended_page(self):
        h = Hospital.objects.create(name='Expired', slug='expired',
                                    expiry_date=date.today() - timedelta(days=1))
        c = self._login_admin_of(h, 'exp@t.com')
        resp = c.get(reverse('medicine_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'saas/suspended.html')

    def test_deactivated_hospital_shows_suspended_page(self):
        h = Hospital.objects.create(name='Off', slug='off', expiry_date=_future(),
                                    is_active=False)
        c = self._login_admin_of(h, 'off@t.com')
        self.assertTemplateUsed(c.get(reverse('medicine_list')), 'saas/suspended.html')

    def test_active_subscription_passes_through(self):
        h = Hospital.objects.create(name='Live', slug='live', expiry_date=_future())
        c = self._login_admin_of(h, 'live@t.com')
        resp = c.get(reverse('medicine_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateNotUsed(resp, 'saas/suspended.html')

    def test_login_stays_reachable_for_a_suspended_tenant(self):
        """Locking out the app must not lock users out of logging in/out."""
        h = Hospital.objects.create(name='Susp', slug='susp',
                                    expiry_date=date.today() - timedelta(days=10))
        c = self._login_admin_of(h, 'susp@t.com')
        self.assertTemplateNotUsed(c.get('/login/'), 'saas/suspended.html')


class HospitalProvisioningTest(TestCase):
    """Creating a tenant from the SaaS portal also creates its first admin."""

    def setUp(self):
        self.root = User.objects.create_superuser(email='root@t.com', password='pw')
        self.client = Client()
        self.client.force_login(self.root)

    def test_create_hospital_with_admin(self):
        resp = self.client.post(reverse('saas:hospital_create'), {
            'name': 'New Clinic', 'slug': 'new-clinic',
            'monthly_price': '1500', 'expiry_date': _future().isoformat(),
            'is_active': 'on', 'modules': ['pharmacy', 'opd'],
            'admin_email': 'admin@newclinic.com', 'admin_password': 'pw12345',
        })
        self.assertEqual(resp.status_code, 302)

        h = Hospital.objects.get(slug='new-clinic')
        self.assertEqual(h.monthly_price, Decimal('1500'))
        self.assertEqual(h.enabled_modules, ['pharmacy', 'opd'])

        admin = User.objects.get(email='admin@newclinic.com')
        self.assertEqual(admin.role, 'ADMIN')
        self.assertEqual(admin.hospital, h)
        self.assertTrue(admin.check_password('pw12345'))

    def test_duplicate_slug_is_rejected(self):
        Hospital.objects.create(name='Taken', slug='taken', expiry_date=_future())
        self.client.post(reverse('saas:hospital_create'), {
            'name': 'Another', 'slug': 'taken',
            'monthly_price': '0', 'expiry_date': _future().isoformat(),
            'admin_email': 'other@t.com', 'admin_password': 'pw12345',
        })
        self.assertEqual(Hospital.objects.filter(slug='taken').count(), 1)
        self.assertFalse(User.objects.filter(email='other@t.com').exists())

    def test_duplicate_admin_email_is_rejected(self):
        User.objects.create_user(email='dupe@t.com', password='pw')
        self.client.post(reverse('saas:hospital_create'), {
            'name': 'Dupe Clinic', 'slug': 'dupe-clinic',
            'monthly_price': '0', 'expiry_date': _future().isoformat(),
            'admin_email': 'dupe@t.com', 'admin_password': 'pw12345',
        })
        self.assertFalse(Hospital.objects.filter(slug='dupe-clinic').exists())
