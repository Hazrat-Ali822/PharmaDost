"""Supplier ledger and tenant scoping.

Suppliers carry a money `balance` (what we owe), so a leak here is both a privacy
and an accounting problem — two hospitals must never share a supplier row.

    python manage.py test suppliers --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital
from suppliers.models import Supplier, SupplierPayment


def _future():
    return date.today() + timedelta(days=365)


class SupplierTenancyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.s1 = Supplier.objects.create(name='Acme Pharma', phone='111', hospital=cls.h1)
        cls.s2 = Supplier.objects.create(name='Beta Distributors', phone='222',
                                         hospital=cls.h2)

    def tearDown(self):
        clear_current_hospital()

    def test_same_supplier_name_allowed_in_different_hospitals(self):
        """Names are unique per hospital, not globally — two tenants can both
        buy from 'Acme Pharma'."""
        dup = Supplier.objects.create(name='Acme Pharma', phone='333', hospital=self.h2)
        self.assertIsNotNone(dup.pk)

    def test_duplicate_supplier_name_within_one_hospital_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Supplier.objects.create(name='Acme Pharma', phone='444', hospital=self.h1)

    def test_manager_scopes_to_bound_hospital(self):
        set_current_hospital(self.h1)
        self.assertEqual(list(Supplier.objects.values_list('name', flat=True)),
                         ['Acme Pharma'])

    def test_supplier_list_excludes_other_tenant(self):
        admin = User.objects.create_user(email='a1@t.com', password='pw',
                                         role='ADMIN', hospital=self.h1)
        c = Client(); c.force_login(admin)
        resp = c.get(reverse('supplier_list'))
        self.assertContains(resp, 'Acme Pharma')
        self.assertNotContains(resp, 'Beta Distributors')

    def test_cannot_open_other_tenant_supplier_ledger(self):
        admin = User.objects.create_user(email='a2@t.com', password='pw',
                                         role='ADMIN', hospital=self.h1)
        c = Client(); c.force_login(admin)
        resp = c.get(reverse('supplier_ledger', args=[self.s2.pk]))
        self.assertIn(resp.status_code, (403, 404))


class SupplierPaymentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        cls.supplier = Supplier.objects.create(name='Acme', phone='111',
                                               balance=Decimal('5000.00'),
                                               hospital=cls.h)
        cls.admin = User.objects.create_user(email='a@t.com', password='pw',
                                             role='ADMIN', hospital=cls.h)

    def tearDown(self):
        clear_current_hospital()

    def _pay(self, client, amount):
        return client.post(reverse('supplier_payment_add', args=[self.supplier.pk]), {
            'amount': amount, 'method': 'CASH', 'notes': 'part payment',
            'date': date.today().isoformat(),
        })

    def test_payment_reduces_supplier_balance(self):
        c = Client(); c.force_login(self.admin)
        resp = self._pay(c, '2000')
        self.assertEqual(resp.status_code, 302)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.balance, Decimal('3000.00'))
        self.assertEqual(SupplierPayment.objects.count(), 1)

    def test_payment_records_who_paid_and_which_tenant(self):
        c = Client(); c.force_login(self.admin)
        self._pay(c, '100')
        payment = SupplierPayment.objects.get()
        self.assertEqual(payment.by_user, self.admin)
        self.assertEqual(payment.hospital, self.h)
