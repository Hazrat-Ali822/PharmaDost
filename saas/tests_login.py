"""Login front-door isolation.

The bare platform domain (sehatyar.online) is the SaaS owner's sign-in only
(+ the public demo); every hospital signs in from its own subdomain
(<slug>.sehatyar.online), which is branded and rejects other tenants' accounts.

    python manage.py test saas.tests_login --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital, hospital_from_host, set_current_hospital, subdomain_slug
from user_mgmt.middleware import SetupMiddleware


def _future():
    return date.today() + timedelta(days=365)


def _skip_setup_wizard():
    """The first-run SetupMiddleware redirects /login/ to /setup/ until a user
    exists, and its `_configured` flag is process-global — so whether it fires in
    a given test depends on run order. These tests are about login routing, not
    first-run, so pin it once users are seeded."""
    SetupMiddleware._configured = True


class SubdomainResolverTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen Health Care', slug='shaheen', expiry_date=_future())

    def test_slug_extraction(self):
        self.assertEqual(subdomain_slug('shaheen.sehatyar.online'), 'shaheen')
        self.assertEqual(subdomain_slug('shaheen.sehatyar.online:443'), 'shaheen')
        self.assertIsNone(subdomain_slug('sehatyar.online'))        # bare domain
        self.assertIsNone(subdomain_slug('www.sehatyar.online'))    # www
        self.assertIsNone(subdomain_slug('a.b.sehatyar.online'))    # deeper label
        self.assertIsNone(subdomain_slug('localhost'))
        self.assertIsNone(subdomain_slug('192.168.1.5'))

    def test_hospital_from_host(self):
        self.assertEqual(hospital_from_host('shaheen.sehatyar.online'), self.h)
        self.assertIsNone(hospital_from_host('nosuch.sehatyar.online'))
        self.assertIsNone(hospital_from_host('sehatyar.online'))


class RootLoginTest(TestCase):
    """Bare domain = SaaS owner only."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen', slug='shaheen', expiry_date=_future())
        set_current_hospital(self.h)
        self.tenant_admin = User.objects.create_user(email='admin@shaheen.com', password='pw', role='ADMIN', hospital=self.h)
        clear_current_hospital()
        self.owner = User.objects.create_superuser(email='owner@sehatyar.online', password='pw')
        _skip_setup_wizard()

    def tearDown(self):
        clear_current_hospital()

    def test_root_login_page_shows_demo(self):
        resp = Client().get(reverse('login'), HTTP_HOST='sehatyar.online')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse('demo_login'))

    def test_superuser_can_sign_in_at_root(self):
        c = Client()
        resp = c.post(reverse('login'), {'username': 'owner@sehatyar.online', 'password': 'pw'},
                      HTTP_HOST='sehatyar.online')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('_auth_user_id', c.session)

    def test_tenant_user_rejected_at_root(self):
        c = Client()
        resp = c.post(reverse('login'), {'username': 'admin@shaheen.com', 'password': 'pw'},
                      HTTP_HOST='sehatyar.online')
        # correct password, but not the owner: NOT logged in, shown their portal
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', c.session)
        self.assertContains(resp, 'hospital portal')          # pointed to their own link
        self.assertContains(resp, 'shaheen.sehatyar.online')


class DesktopLanLoginTest(TestCase):
    """Desktop / LAN build: every staff account signs in at the same front door.

    On the LAN a phone reaches the server at an IP (192.168.x.x), which never
    resolves a tenant by host, so the hosted RootLoginView would admit only the
    superuser and lock out every nurse/receptionist/doctor. Regression guard for
    that: with DESKTOP_BUILD on, a plain non-superuser signs in normally.
    """

    def setUp(self):
        # Desktop staff are hospital-less non-superusers (the desktop build has no
        # tenant), created by the clinic's own admin.
        self.nurse = User.objects.create_user(
            email='nurse@clinic.local', password='pw', role='NURSE')
        _skip_setup_wizard()

    @override_settings(DESKTOP_BUILD=True, ALLOWED_HOSTS=['*'])
    def test_non_superuser_signs_in_on_lan_ip(self):
        c = Client()
        resp = c.post(reverse('login'),
                      {'username': 'nurse@clinic.local', 'password': 'pw'},
                      HTTP_HOST='192.168.1.5:8000')
        self.assertEqual(resp.status_code, 302, "LAN staff could not sign in")
        self.assertIn('_auth_user_id', c.session)

    def test_hosted_bare_domain_still_owner_only(self):
        """The fix must not weaken the hosted platform: a non-superuser is still
        turned away at the bare domain when DESKTOP_BUILD is off."""
        c = Client()
        resp = c.post(reverse('login'),
                      {'username': 'nurse@clinic.local', 'password': 'pw'},
                      HTTP_HOST='sehatyar.online')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', c.session)


class SubdomainLoginTest(TestCase):
    """A hospital subdomain = that hospital's branded, isolated sign-in."""

    def setUp(self):
        self.a = Hospital.objects.create(name='Shaheen Health Care', slug='shaheen', expiry_date=_future())
        self.b = Hospital.objects.create(name='Gulshan Clinic', slug='gulshan', expiry_date=_future())
        set_current_hospital(self.a)
        self.a_user = User.objects.create_user(email='a@shaheen.com', password='pw', role='ADMIN', hospital=self.a)
        set_current_hospital(self.b)
        self.b_user = User.objects.create_user(email='b@gulshan.com', password='pw', role='ADMIN', hospital=self.b)
        clear_current_hospital()
        _skip_setup_wizard()

    def tearDown(self):
        clear_current_hospital()

    def test_subdomain_shows_that_hospital(self):
        resp = Client().get(reverse('login'), HTTP_HOST='shaheen.sehatyar.online')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Shaheen Health Care')

    def test_own_user_signs_in_on_subdomain(self):
        c = Client()
        resp = c.post(reverse('login'), {'email': 'a@shaheen.com', 'password': 'pw'},
                      HTTP_HOST='shaheen.sehatyar.online')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('_auth_user_id', c.session)

    def test_other_hospital_user_rejected_on_subdomain(self):
        c = Client()
        resp = c.post(reverse('login'), {'email': 'b@gulshan.com', 'password': 'pw'},
                      HTTP_HOST='shaheen.sehatyar.online')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', c.session)
        self.assertContains(resp, 'does not belong')


class TenantDoorLooksLikeItsOwnHospitalTest(TestCase):
    """The tenant sign-in page shares the platform page's LOOK, not its content.

    It gained the photograph, the floating card and the brand wash; it must not
    gain the marketing navbar or the "Try the live demo" button, which would send
    a hospital's own staff into somebody else's sample data.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen Health Care', slug='shaheen',
                                         expiry_date=_future())
        set_current_hospital(self.h)
        from user_mgmt.models import SiteSettings
        row = SiteSettings.load()
        row.primary_color = '#0d7c6d'
        row.accent_color = '#43bda8'
        row.save()
        User.objects.create_user(email='a@shaheen.com', password='pw',
                                 role='ADMIN', hospital=self.h)
        clear_current_hospital()
        _skip_setup_wizard()

    def tearDown(self):
        clear_current_hospital()

    def _page(self):
        return Client().get('/shaheen/login/')

    def test_it_wears_the_hospitals_own_colour(self):
        body = self._page().content.decode()
        self.assertIn('#0d7c6d', body)
        # ...and that colour is what the wash over the photograph is mixed from,
        # which is the whole point of it being per-tenant.
        self.assertIn('color-mix(in srgb, var(--primary)', body)

    def test_it_carries_no_platform_marketing(self):
        body = self._page().content.decode()
        self.assertNotIn('site-nav', body)               # the marketing navbar
        self.assertNotIn('Try the live demo', body)
        self.assertNotIn('/features/', body)

    def test_the_headline_is_the_hospital(self):
        self.assertContains(self._page(), 'Shaheen Health Care')

    def test_forgot_password_is_offered_and_comes_back_here(self):
        body = self._page().content.decode()
        self.assertIn('Forgot password?', body)
        # The `next` matters on the path form: without it the reset page's "back"
        # link resolves to the owner-only platform door, which refuses this
        # hospital's staff — a dead end one tap from where they started.
        self.assertIn('password_reset/?next=/shaheen/login/', body)


class ForgotPasswordPageTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen Health Care', slug='shaheen',
                                         expiry_date=_future())
        _skip_setup_wizard()

    def tearDown(self):
        clear_current_hospital()

    def test_it_names_the_hospital_it_was_reached_from(self):
        resp = Client().get(reverse('password_reset'), {'next': '/shaheen/login/'})
        self.assertContains(resp, 'Shaheen Health Care')
        self.assertContains(resp, 'href="/shaheen/login/"')

    def test_the_subdomain_route_needs_no_next_at_all(self):
        resp = Client().get(reverse('password_reset'),
                            HTTP_HOST='shaheen.sehatyar.online')
        self.assertContains(resp, 'Shaheen Health Care')

    def test_an_off_site_next_is_refused(self):
        # An unchecked `next` makes this page an open redirect, which is a
        # phishing step: the user is on the real hospital domain, asks for a
        # reset, and is handed a copy of the sign-in form.
        resp = Client().get(reverse('password_reset'),
                            {'next': 'https://evil.example/login/'})
        self.assertNotContains(resp, 'evil.example')

    def test_it_still_says_no_email_is_sent(self):
        # There is no mail backend. A page that implies a link is on its way
        # leaves staff waiting for something that never arrives.
        self.assertContains(Client().get(reverse('password_reset')), 'no email is sent')

    def test_the_platform_door_keeps_its_own_branding(self):
        resp = Client().get(reverse('password_reset'))
        self.assertNotContains(resp, 'Shaheen Health Care')
