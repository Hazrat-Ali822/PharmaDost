"""Login front-door isolation.

The bare platform domain (sehatyar.online) is the SaaS owner's sign-in only
(+ the public demo); every hospital signs in from its own subdomain
(<slug>.sehatyar.online), which is branded and rejects other tenants' accounts.

    python manage.py test saas.tests_login --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
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
