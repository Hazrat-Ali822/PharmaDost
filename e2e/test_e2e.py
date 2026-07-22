"""End-to-end tests: a real browser against a real server.

Everything else in the suite talks to Django's test client, which never runs the
JavaScript. These tests catch what that misses — a broken POS cart script, a form
that never submits, a permission that hides a link in the DOM but not in the URL.

Setup (one time):

    pip install -r requirements-dev.txt
    playwright install chromium

Run:

    python manage.py test e2e --settings=pharma_mgmt.test_settings

They skip themselves — never fail — when Playwright or its browser is missing, so
`manage.py test` stays green on a machine that has not installed them.
"""
import os
import unittest
from datetime import date, timedelta
from decimal import Decimal

# Playwright's sync API drives a greenlet event loop, which Django's ORM detects
# as an async context and refuses to run in. The live server here is a plain
# thread, so the check is a false positive — opt out before Django is touched.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402

from accounts.models import User
from inventory.models import Medicine
from saas.models import Hospital

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_INSTALLED = True
except ImportError:                                        # pragma: no cover
    PLAYWRIGHT_INSTALLED = False


def _future():
    return date.today() + timedelta(days=365)


@unittest.skipUnless(PLAYWRIGHT_INSTALLED,
                     "playwright not installed — pip install -r requirements-dev.txt")
class BrowserTestCase(StaticLiveServerTestCase):
    """Boots one Chromium for the class and a fresh page per test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._playwright = sync_playwright().start()
        try:
            cls.browser = cls._playwright.chromium.launch()
        except Exception as exc:                           # pragma: no cover
            cls._playwright.stop()
            super().tearDownClass()
            raise unittest.SkipTest(
                f"Chromium unavailable ({exc}). Run: playwright install chromium"
            )

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, 'browser', None)
        if browser:
            browser.close()
        playwright = getattr(cls, '_playwright', None)
        if playwright:
            playwright.stop()
        super().tearDownClass()

    def setUp(self):
        # NOTE: LiveServerTestCase extends TransactionTestCase, which does NOT
        # support setUpTestData — fixtures must be built per test, in setUp.
        # Without a user the SetupMiddleware sends every request to /setup/.
        self.hospital = Hospital.objects.create(name='E2E Hospital', slug='e2e',
                                                expiry_date=_future())
        self.admin = User.objects.create_user(email='e2e-admin@test.com',
                                              password='pw12345', role='ADMIN',
                                              hospital=self.hospital)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(10_000)

    def tearDown(self):
        self.page.close()

    # --- helpers ---------------------------------------------------------
    def url(self, path):
        return f"{self.live_server_url}{path}"

    def login(self, email, password='pw12345'):
        self.page.goto(self.url('/login/'))
        self.page.fill('input[name="username"]', email)
        self.page.fill('input[name="password"]', password)
        self.page.click('button[type="submit"], input[type="submit"]')
        self.page.wait_for_load_state('networkidle')


class LoginFlowTest(BrowserTestCase):
    def test_valid_login_reaches_the_dashboard(self):
        self.login('e2e-admin@test.com')
        self.assertNotIn('/login', self.page.url)
        self.assertIn('Welcome back', self.page.content())

    def test_invalid_password_stays_on_login(self):
        self.login('e2e-admin@test.com', 'wrong-password')
        self.assertIn('/login', self.page.url)

    def test_logout_returns_to_login(self):
        self.login('e2e-admin@test.com')
        self.page.goto(self.url('/logout/'))
        self.page.goto(self.url('/'))
        self.assertIn('/login', self.page.url)


class MedicineFlowTest(BrowserTestCase):
    def test_add_a_medicine_through_the_form(self):
        self.login('e2e-admin@test.com')
        self.page.goto(self.url('/medicines/add/'))

        self.page.fill('input[name="name"]', 'Paracetamol E2E')
        self.page.fill('input[name="brand"]', 'Panadol')
        self.page.fill('input[name="price"]', '25')
        self.page.fill('input[name="expiry_date"]', _future().isoformat())
        self.page.click('button[type="submit"]')
        self.page.wait_for_load_state('networkidle')

        med = Medicine.objects.filter(name='Paracetamol E2E').first()
        self.assertIsNotNone(med, "medicine was not saved through the browser form")
        self.assertEqual(med.hospital, self.hospital)
        self.assertIn('Paracetamol E2E', self.page.content())


class PosCartTest(BrowserTestCase):
    """The POS cart is the most JavaScript-heavy screen in the product."""

    def setUp(self):
        super().setUp()
        self.med = Medicine.objects.create(name='CartMed', brand='B',
                                           price=Decimal('50'), expiry_date=_future(),
                                           hospital=self.hospital)
        self.med.add_stock(100, expiry_date=_future(), cost_price=Decimal('30'))

    def test_pos_page_loads_with_its_scripts(self):
        self.login('e2e-admin@test.com')
        errors = []
        self.page.on('pageerror', lambda e: errors.append(str(e)))
        self.page.goto(self.url('/sales/new/'))
        self.page.wait_for_load_state('networkidle')

        self.assertEqual(errors, [], f"JavaScript errors on the POS page: {errors}")
        self.assertIn('Create Bill', self.page.content())


class RoleVisibilityTest(BrowserTestCase):
    """What a role cannot use must not be in their sidebar."""

    def setUp(self):
        super().setUp()
        self.nurse = User.objects.create_user(email='e2e-nurse@test.com',
                                              password='pw12345', role='NURSE',
                                              hospital=self.hospital)

    def test_nurse_sidebar_has_ward_but_no_pharmacy_or_billing(self):
        """Assert against the sidebar only — the page also ships a keyboard-shortcut
        guide that lists URLs regardless of permission."""
        self.login('e2e-nurse@test.com')
        nav = self.page.locator('aside.sidebar nav.nav').inner_html()
        self.assertIn('/ipd/', nav, "nurse has no Ward link in the sidebar")
        self.assertNotIn('/sales/new/', nav)
        self.assertNotIn('/billing/', nav)

    def test_nurse_typing_the_pos_url_is_refused(self):
        """Hiding a link is presentation; the server must still say no."""
        self.login('e2e-nurse@test.com')
        response = self.page.goto(self.url('/sales/new/'))
        self.assertEqual(response.status, 403)
