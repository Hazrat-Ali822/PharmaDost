"""Regression cover for bugs reported from real use.

Each of these was found by someone looking at a live screen, not by the suite.
The fixes are in place; these keep them there.
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital
from user_mgmt.models import SiteSettings


class PharmacyOnlyTenantTest(TestCase):
    """Reported: a pharmacy-only hospital still saw OPD, Laboratory and Radiology
    tiles on its dashboard — departments it does not have."""

    def setUp(self):
        self.h = Hospital.objects.create(
            name='Gull Pharmacy', slug='gull',
            expiry_date=date.today() + timedelta(days=30),
            enabled_modules=['pharmacy', 'finance', 'reports'])
        self.admin = User.objects.create_user(email='g@x.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def test_the_dashboard_hides_departments_the_tenant_did_not_buy(self):
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Laboratory', body)
        self.assertNotIn('Radiology', body)

    def test_the_sidebar_hides_them_too(self):
        """A tile and its nav link have to agree, or one of them is a link into a 403."""
        body = self.client.get(reverse('dashboard')).content.decode()
        sidebar = body.split('<aside', 1)[-1].split('</aside>', 1)[0]
        self.assertNotIn("/lab/", sidebar)
        self.assertNotIn("/imaging/", sidebar)

    def test_the_pharmacy_it_did_buy_is_still_there(self):
        sidebar = (self.client.get(reverse('dashboard')).content.decode()
                   .split('<aside', 1)[-1].split('</aside>', 1)[0])
        self.assertIn('/medicines/', sidebar)


class SaasPortalBrandingTest(TestCase):
    """Reported: a colour set by one hospital showed up on the superuser's SaaS
    portal. Tenant branding must stop at the tenant."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        SiteSettings.objects.create(hospital=self.h, brand_name='Shaheen',
                                    primary_color='#ff0099', accent_color='#00ff11')
        self.owner = User.objects.create_superuser(email='owner@x.com', password='pw')
        self.client = Client()
        self.client.force_login(self.owner)

    def tearDown(self):
        clear_current_hospital()

    def test_a_tenants_colour_does_not_reach_the_saas_portal(self):
        body = self.client.get(reverse('saas:dashboard')).content.decode()
        self.assertNotIn('#ff0099', body)
        self.assertNotIn('#00ff11', body)

    def test_the_tenants_own_pages_still_get_their_colour(self):
        """The guard must be about the portal, not about switching branding off."""
        staff = User.objects.create_user(email='s@x.com', password='pw',
                                         role='ADMIN', hospital=self.h)
        c = Client()
        c.force_login(staff)
        body = c.get(reverse('user_mgmt:admin_dashboard')).content.decode()
        self.assertIn('#ff0099', body)
