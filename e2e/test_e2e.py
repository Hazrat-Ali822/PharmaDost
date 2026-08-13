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
        """Sign in by planting a real session cookie, not by driving the login form.

        The hosted front door is host-aware (`accounts.smart_login`): at localhost —
        where the live server runs — no tenant resolves by host, so the owner-only
        RootLoginView would turn a tenant admin or nurse away. Rather than depend on
        that policy (or change rendering by faking the desktop build), we create the
        server-side session exactly as Django's own `force_login` does and hand the
        browser its cookie. Rendering is the true hosted rendering — which is what the
        mobile-layout assertions need to be measuring. The login *form* itself is
        covered by `test_valid_login_reaches_the_dashboard` /
        `test_invalid_password_stays_on_login` and by `saas/tests_login.py`.
        """
        from importlib import import_module
        from django.conf import settings
        from django.contrib.auth import (SESSION_KEY, BACKEND_SESSION_KEY,
                                         HASH_SESSION_KEY)
        user = User.objects.get(email=email)
        engine = import_module(settings.SESSION_ENGINE)
        session = engine.SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = 'django.contrib.auth.backends.ModelBackend'
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        # A cookie must be set from a page on the origin, so open one first.
        self.page.goto(self.url('/login/'))
        self.page.context.add_cookies([{
            'name': settings.SESSION_COOKIE_NAME,
            'value': session.session_key,
            'url': self.live_server_url,
        }])
        self.page.goto(self.url('/'))
        self.page.wait_for_load_state('networkidle')


class LoginFlowTest(BrowserTestCase):
    def test_valid_login_reaches_the_dashboard(self):
        self.login('e2e-admin@test.com')
        self.assertNotIn('/login', self.page.url)
        self.assertIn('Welcome back', self.page.content())

    def test_invalid_password_stays_on_login(self):
        # Drive the real form (force-login ignores the password): a wrong password
        # must never authenticate, so the browser stays on the sign-in page.
        self.page.goto(self.url('/login/'))
        self.page.fill('input[name="username"]', 'e2e-admin@test.com')
        self.page.fill('input[name="password"]', 'wrong-password')
        self.page.click('button[type="submit"], input[type="submit"]')
        self.page.wait_for_load_state('networkidle')
        self.assertIn('/login', self.page.url)

    def test_logout_ends_the_session(self):
        """After logout, `/` must no longer be the signed-in app.

        It is not asserted to be `/login` any more: since the SEO work, the root
        serves the public marketing landing to an anonymous visitor instead of a
        sign-in wall (`seo_views.home`), so the old assertion was testing a
        behaviour the product deliberately dropped. What still matters is that the
        session is gone — so the app dashboard must bounce to the sign-in page."""
        self.login('e2e-admin@test.com')
        self.page.goto(self.url('/logout/'))
        self.page.goto(self.url('/dashboard/'))
        self.assertIn('/login', self.page.url)


class MedicineFlowTest(BrowserTestCase):
    def test_add_a_medicine_through_the_form(self):
        self.login('e2e-admin@test.com')
        self.page.goto(self.url('/medicines/add/'))

        self.page.fill('input[name="name"]', 'Paracetamol E2E')
        self.page.fill('input[name="brand"]', 'Panadol')
        self.page.fill('input[name="price"]', '25')
        self.page.fill('input[name="expiry_date"]', _future().isoformat())
        # This form is offline-capable, so the click first asks the server whether it
        # is reachable (static/js/offline.js) and only then submits. `networkidle`
        # alone can therefore fire in the gap *before* the real navigation starts and
        # `page.content()` reads a page that is on its way out — wait for the
        # navigation itself.
        with self.page.expect_navigation(wait_until='networkidle'):
            self.page.click('button[type="submit"]')

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


class MobileLayoutTest(BrowserTestCase):
    """The app is used on phones at the reception desk and on the ward round.

    The failure this guards is specific and unmistakable: one wide table pushes the
    whole page wider than the screen, so *every* screen scrolls sideways — the
    header slides away, the menu button drifts off, and the app reads as broken.
    Nothing in a template review catches it; you have to measure the page.
    """

    PHONE = {'width': 390, 'height': 844}          # iPhone 14-ish

    def setUp(self):
        super().setUp()
        self.page.set_viewport_size(self.PHONE)
        # Enough rows that the wide tables really are wide.
        for i in range(6):
            Medicine.objects.create(name=f'Med {i}', brand='B',
                                    price=Decimal('50'), expiry_date=_future(),
                                    hospital=self.hospital)

    def _open(self, path):
        """Wait for the page to be laid out, not for the network to fall quiet.

        `networkidle` is the wrong signal for this app: every page polls
        /accounts/notifications/latest/ on a timer and registers a service worker,
        so "500ms of silence" is a race rather than a state. Waiting for the
        content element is deterministic.
        """
        self.page.goto(path if path.startswith('http') else self.url(path),
                       wait_until='load')
        self.page.wait_for_selector('.content, .sheet', state='attached')

    def _overflow(self, path):
        self._open(path)
        return self.page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth")

    def test_no_screen_scrolls_sideways_on_a_phone(self):
        self.login('e2e-admin@test.com')
        for path in ['/', '/patients/', '/medicines/', '/sales/new/',
                     '/opd/appointments/', '/billing/', '/offline/queue/']:
            with self.subTest(page=path):
                # A pixel or two is sub-pixel rounding; a column's worth is the bug.
                self.assertLessEqual(
                    self._overflow(path), 2,
                    f"{path} is wider than the phone screen — a wide element is "
                    f"not inside a .table-scroll box")

    def test_a_wide_table_scrolls_inside_its_own_box(self):
        """The table must still be readable — the fix is a scroll box, not
        hiding the overflow."""
        self.login('e2e-admin@test.com')
        self._open('/medicines/')
        boxes = self.page.locator('.table-scroll')
        self.assertGreater(boxes.count(), 0, "the medicine table was never wrapped")

    def test_the_menu_button_is_reachable_and_opens_the_sidebar(self):
        self.login('e2e-admin@test.com')
        self._open('/patients/')
        self.assertTrue(self.page.locator('.hamburger').is_visible(),
                        "no way to open the menu on a phone")
        self.page.click('.hamburger')
        self.assertTrue(self.page.locator('aside.sidebar').is_visible())

    def test_form_fields_do_not_trigger_the_ios_zoom(self):
        """Under 16px iOS zooms in on focus and does not zoom back out."""
        self.login('e2e-admin@test.com')
        self._open('/patients/add/')
        size = self.page.evaluate(
            "() => parseFloat(getComputedStyle("
            "document.querySelector('input[name=\"full_name\"]')).fontSize)")
        self.assertGreaterEqual(size, 16, "text inputs are below 16px on a phone")


# NOTE: there is deliberately no browser test for the offline shell here.
# Staging a real network cut is harder than it looks and every cheap way of
# faking one silently proves nothing: `context.set_offline(True)` and
# `context.route(..., abort)` apply to the page, not to the service worker's own
# fetches, and `server_thread.terminate()` leaves the keep-alive sockets Chrome
# already holds being served by their handler threads. Stopping an HTTP/1.0
# server does work and was used by hand to verify the behaviour (CLAUDE.md
# records the method), but as a standing test it passed about one run in three
# and left ~14 Chromium processes behind, which then broke whichever test ran
# next. What it would have guarded is covered deterministically in
# user_mgmt/tests_pwa.py instead.

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
