"""The install-as-app (PWA) plumbing: per-tenant manifest, icon, service worker."""
import json
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital
from user_mgmt.models import SiteSettings


class PwaTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen Health Care', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        SiteSettings.objects.create(hospital=self.h, brand_name='Shaheen Health Care',
                                    primary_color='#1a7f37', logo_text='S')
        self.user = User.objects.create_user(email='a@x.com', password='pw',
                                             role='ADMIN', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def test_manifest_carries_the_tenants_name_and_colour(self):
        resp = self.client.get(reverse('pwa_manifest'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('application/manifest+json', resp['Content-Type'])
        data = json.loads(resp.content)
        self.assertEqual(data['name'], 'Shaheen Health Care')
        self.assertEqual(data['theme_color'], '#1a7f37')
        self.assertEqual(data['display'], 'standalone')
        self.assertTrue(data['icons'])

    def test_icon_renders_a_png(self):
        resp = self.client.get(reverse('pwa_icon', args=[192]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')
        self.assertTrue(resp.content.startswith(b'\x89PNG'))

    def test_service_worker_is_served_at_root_scope(self):
        resp = self.client.get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('javascript', resp['Content-Type'])
        self.assertIn('/offline/', resp.content.decode())

    def test_the_pwa_endpoints_are_reachable_without_logging_in(self):
        """The browser fetches the manifest/sw/offline without a session — if the
        login middleware redirected them, install would break."""
        anon = Client()
        for name in ('pwa_manifest', 'pwa_offline'):
            self.assertEqual(anon.get(reverse(name)).status_code, 200, name)
        self.assertEqual(anon.get('/sw.js').status_code, 200)

    def test_get_app_page_offers_install(self):
        resp = self.client.get(reverse('get_app'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Install')
        self.assertContains(resp, 'Add to Home Screen')      # the iPhone route

    def test_the_head_links_the_manifest_and_registers_the_worker(self):
        body = self.client.get(reverse('get_app')).content.decode()
        self.assertIn('rel="manifest"', body)
        self.assertIn("serviceWorker", body)
