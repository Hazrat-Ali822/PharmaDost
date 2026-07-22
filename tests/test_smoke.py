"""Smoke / sanity tests.

The fastest useful check: log in as an admin of a fully-enabled hospital and open
every major page. Catches template syntax errors, bad {% url %} names, missing
context and import errors across the whole app in one run — the class of breakage
that makes the product unusable rather than subtly wrong.

Deliberately GET-only and data-free: every page must render on an empty database.

    python manage.py test tests.test_smoke --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital


# Pages an admin should be able to open. GET-safe and renderable with no data.
ADMIN_PAGES = [
    # core
    'dashboard', 'dashboard_page',
    # pharmacy / inventory
    'medicine_list', 'medicine_add', 'expiry_report', 'reorder_report',
    'inventory_analytics', 'po_list', 'po_create', 'purchase_list', 'purchase_create',
    'adjustment_list', 'adjustment_create', 'preturn_list', 'preturn_create',
    # sales
    'sale_create', 'sale_list', 'wholesale_order_list', 'wholesale_order_create',
    # people
    'customer_list', 'customer_add', 'supplier_list', 'supplier_add',
    'patient_list', 'patient_add',
    # clinical
    'doctor_list', 'doctor_add', 'appointment_list', 'appointment_add',
    'prescription_list', 'prescription_presets', 'preset_create',
    # finance
    'invoice_list', 'invoice_create', 'patient_billing_list',
    'expense_list', 'expense_create', 'cash_closing_list', 'cash_closing_new',
    'payout_list',
    # reports
    'sales_report', 'inventory_report', 'profit_report', 'daybook_report',
    'visual_analytics',
    # system
    'audit_log',
]

ADMIN_NAMESPACED_PAGES = [
    'lab:order_list', 'lab:order_create', 'lab:test_catalog',
    'imaging:study_list', 'imaging:study_create', 'imaging:scan_catalog',
    'ipd:admission_list', 'ipd:admission_create', 'ipd:admission_request_list',
    'ipd:ward_bed_list', 'ipd:ward_create', 'ipd:bed_create',
    'ot:surgery_list', 'ot:surgery_create', 'ot:surgery_request_list',
    'ot:procedure_list', 'ot:procedure_create', 'ot:category_create',
    'user_mgmt:user_list', 'user_mgmt:user_create', 'user_mgmt:site_settings',
]


class AdminSmokeTest(TestCase):
    """Every major page renders for a hospital admin."""

    @classmethod
    def setUpTestData(cls):
        cls.hospital = Hospital.objects.create(
            name='Smoke Hospital', slug='smoke',
            expiry_date=date.today() + timedelta(days=365),
        )
        cls.admin = User.objects.create_user(
            email='smoke-admin@test.com', password='pw', role='ADMIN',
            hospital=cls.hospital,
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def test_admin_pages_render(self):
        for name in ADMIN_PAGES + ADMIN_NAMESPACED_PAGES:
            with self.subTest(page=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(
                    resp.status_code, 200,
                    f"{name} returned {resp.status_code}, expected 200",
                )

    def test_post_login_router_sends_admin_to_dashboard(self):
        resp = self.client.get(reverse('user_mgmt:post_login_redirect'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('dashboard'))

    def test_notification_endpoints(self):
        """The sidebar polls this every 5s — a 500 here spams every logged-in user."""
        resp = self.client.get(reverse('accounts:get_notifications_latest'))
        self.assertEqual(resp.status_code, 200)


class SuperuserSmokeTest(TestCase):
    """The SaaS portal is superuser-only and lives outside tenant scope."""

    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser(email='root@test.com', password='pw')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.root)

    def test_saas_portal_pages_render(self):
        for name in ['saas:dashboard', 'saas:hospital_create',
                     'saas:payment_create', 'saas:expense_create']:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class AnonymousSmokeTest(TestCase):
    """Public surface: the login page must work; everything else must not leak."""

    def test_login_page_renders(self):
        self.assertEqual(Client().get(reverse('login')).status_code, 200)

    def test_home_redirects_anonymous_to_login(self):
        resp = Client().get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])
