"""The install-as-app (PWA) plumbing: per-tenant manifest, icon, service worker."""
import json
from datetime import date, timedelta
from pathlib import Path

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

    def test_every_pre_cached_shell_url_actually_resolves(self):
        """A shell path that 404s caches nothing, and the screen is simply absent
        when the connection drops — silently, which is the worst way to find out.
        Two of them (`/opd/visit/`, `/lab/order/new/`) sat in this list dead."""
        from django.urls import Resolver404, resolve
        from user_mgmt.pwa_views import _shell_urls

        for url in _shell_urls():
            if url.startswith('/static/'):
                continue
            try:
                resolve(url)
            except Resolver404:
                self.fail(f"service-worker shell url does not resolve: {url}")

    def test_the_shell_covers_the_offline_capable_screens(self):
        """Each offline form must have its page pre-cached, or it cannot be opened
        to be filled in without a connection."""
        from user_mgmt.pwa_views import _shell_urls
        shell = set(_shell_urls())
        for name in ('patient_add', 'sale_create', 'visit_create', 'offline_queue',
                     'lab:order_create', 'imaging:study_create',
                     'ipd:admission_create', 'ot:surgery_create', 'expense_create'):
            self.assertIn(reverse(name), shell, name)

    def test_signing_in_pre_caches_every_screen_for_the_outage(self):
        """The worker's body is per-user, so signing in installs a *new* worker,
        whose `install` step fetches the whole shell. That is what makes a device
        ready for an outage without the staff having had to visit each screen —
        so the shell must actually be inside the served file."""
        body = self.client.get('/sw.js').content.decode()
        for name in ('patient_add', 'sale_create', 'lab:order_create',
                     'ipd:admission_create', 'expense_create', 'offline_queue'):
            self.assertIn(f'"{reverse(name)}"', body, name)

    def test_the_worker_is_per_user_so_a_shared_device_does_not_leak_pages(self):
        """The worker caches rendered pages; two users on one device must not
        share a cache, or the next person sees the last one's patients."""
        first = self.client.get('/sw.js').content.decode()
        other = User.objects.create_user(email='b@x.com', password='pw',
                                         role='ADMIN', hospital=self.h)
        c2 = Client(); c2.force_login(other)
        second = c2.get('/sw.js').content.decode()
        self.assertNotEqual(first, second)

    def test_get_app_page_offers_install(self):
        resp = self.client.get(reverse('get_app'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Install')
        self.assertContains(resp, 'Add to Home Screen')      # the iPhone route

    def test_the_head_links_the_manifest_and_registers_the_worker(self):
        body = self.client.get(reverse('get_app')).content.decode()
        self.assertIn('rel="manifest"', body)
        self.assertIn("serviceWorker", body)


class LanConnectTest(TestCase):
    """The desktop build can act as the clinic's own server on the wifi, so every
    phone works with no internet at all. This is the page that tells staff how to
    join — useless if it does not show the actual address."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(email='a@x.com', password='pw',
                                             role='ADMIN', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def test_it_shows_the_lan_address_when_running_as_a_local_server(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'PHARMADOST_LAN_URL': 'http://192.168.1.7:8000/',
                                     'PHARMADOST_LAN_IPS': '192.168.1.7'}):
            resp = self.client.get(reverse('lan_connect'))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'http://192.168.1.7:8000/')
            # and the sidebar offers it on every page
            self.assertContains(self.client.get(reverse('get_app')), 'Connect a Device')

    def test_it_says_so_plainly_on_the_hosted_site(self):
        """No LAN url means the hosted site — the page must not show a blank card
        or a dead address."""
        resp = self.client.get(reverse('lan_connect'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Not running as a local server')

    def test_the_sidebar_link_is_hidden_without_a_lan_server(self):
        self.assertNotContains(self.client.get(reverse('get_app')), 'Connect a Device')

    def test_a_second_interface_is_offered_as_a_fallback_address(self):
        """Wifi + ethernet both present: staff must be told the other one, or a
        phone on the wifi silently fails against the ethernet address."""
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'PHARMADOST_LAN_URL': 'http://10.0.0.5:8000/',
                                     'PHARMADOST_LAN_IPS': '10.0.0.5,192.168.1.7'}):
            resp = self.client.get(reverse('lan_connect'))
            self.assertContains(resp, 'http://192.168.1.7:8000/')


class LauncherLanTest(TestCase):
    """`desktop/launcher.py` decides what Django will accept. Get these wrong and
    every phone gets 'Bad Request (400)' or a CSRF failure on every form."""

    def _launcher(self):
        import importlib.util
        from django.conf import settings
        path = Path(settings.BASE_DIR) / 'desktop' / 'launcher.py'
        spec = importlib.util.spec_from_file_location('_launcher_under_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lan_mode_is_on_by_default_and_can_be_turned_off(self):
        import os
        from unittest.mock import patch
        launcher = self._launcher()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PHARMADOST_LAN', None)
            self.assertTrue(launcher.lan_enabled())
        with patch.dict(os.environ, {'PHARMADOST_LAN': '0'}):
            self.assertFalse(launcher.lan_enabled())

    def test_the_lan_address_is_allowed_and_csrf_trusted(self):
        """Django rejects a Host it does not know and a POST Origin it does not
        trust; both lists must name the LAN address or nothing works from a phone."""
        import os
        from unittest.mock import patch
        launcher = self._launcher()
        hosts = ['127.0.0.1', 'localhost', '192.168.1.7', 'CLINIC-PC']
        # patch.dict restores the whole environment on exit, so assert inside it.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DJANGO_SSL', None)
            with patch.object(launcher, 'data_dir', return_value=Path('.')):
                with patch.object(launcher, '_ensure_secret_key', lambda dd: None):
                    launcher.configure_environment(hosts, 8000)
            self.assertIn('192.168.1.7', os.environ['DJANGO_ALLOWED_HOSTS'])
            self.assertIn('http://192.168.1.7:8000', os.environ['DJANGO_CSRF_TRUSTED'])
            # plain http on the LAN — secure-only cookies would stop login working
            self.assertEqual(os.environ['DJANGO_SSL'], 'false')

    def test_loopback_and_link_local_are_not_offered_as_lan_addresses(self):
        """127.x is not reachable from a phone and 169.254.x means the router never
        gave this machine an address — offering either sends staff chasing a dead one."""
        from unittest.mock import patch
        launcher = self._launcher()
        with patch('socket.gethostbyname_ex',
                   return_value=('pc', [], ['127.0.0.1', '169.254.3.4', '192.168.1.7'])):
            with patch('socket.socket', side_effect=OSError):   # no default route
                self.assertEqual(launcher.lan_addresses(), ['192.168.1.7'])

    def test_the_port_stays_the_same_between_launches(self):
        """Staff bookmark http://<ip>:8000 on a phone. A random port each morning
        would break that bookmark every day."""
        launcher = self._launcher()
        self.assertEqual(launcher.pick_port('127.0.0.1', 8000), 8000)
