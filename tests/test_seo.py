"""The public SEO / AEO surface must be reachable by an anonymous crawler and
carry the content that makes it indexable and citable.

    python manage.py test tests.test_seo --settings=pharma_mgmt.test_settings
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from user_mgmt.middleware import SetupMiddleware


class SeoPublicSurfaceTest(TestCase):
    def setUp(self):
        # A user must exist or SetupMiddleware sends everything to the first-run
        # wizard; these pages are about the crawler's view, not first-run.
        get_user_model().objects.create_superuser(email='owner@x.com', password='pw')
        SetupMiddleware._configured = True
        self.c = Client()                      # anonymous — a crawler has no session

    def test_landing_is_public_and_structured(self):
        r = self.c.get('/features/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Hospital &amp; Pharmacy Management System')
        # structured data for search + AI answer engines
        self.assertContains(r, 'application/ld+json')
        self.assertContains(r, 'SoftwareApplication')
        self.assertContains(r, 'WebSite')
        self.assertContains(r, 'FAQPage')
        self.assertContains(r, 'name="description"')
        self.assertContains(r, 'og:title')

    def test_root_serves_the_landing_to_anonymous(self):
        """The homepage a crawler indexes must be real content, not a login wall."""
        r = self.c.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Hospital &amp; Pharmacy Management System')
        self.assertContains(r, 'SoftwareApplication')
        # canonical points at the bare root so / and /features/ don't compete
        self.assertContains(r, 'rel="canonical"')

    def test_robots_txt(self):
        r = self.c.get('/robots.txt')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/plain'))
        self.assertContains(r, 'Sitemap:')

    def test_sitemap_xml(self):
        r = self.c.get('/sitemap.xml')
        self.assertEqual(r.status_code, 200)
        self.assertIn('xml', r['Content-Type'])
        self.assertContains(r, '/features/')

    def test_llms_txt(self):
        r = self.c.get('/llms.txt')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Sehatyar')
        self.assertContains(r, 'Live demo')

    def test_none_are_login_walled(self):
        for path in ['/features/', '/robots.txt', '/sitemap.xml', '/llms.txt']:
            self.assertEqual(self.c.get(path).status_code, 200,
                             f'{path} redirected an anonymous crawler')

    def test_keyword_content_pages_are_public_and_structured(self):
        from user_mgmt.seo_views import CONTENT_PAGES
        self.assertTrue(CONTENT_PAGES)
        sitemap = self.c.get('/sitemap.xml').content.decode()
        for slug, page in CONTENT_PAGES.items():
            r = self.c.get(f'/{slug}/')
            self.assertEqual(r.status_code, 200, f'/{slug}/ not reachable anonymously')
            self.assertContains(r, page['h1'])
            self.assertContains(r, 'application/ld+json')
            self.assertContains(r, 'BreadcrumbList')
            self.assertContains(r, 'rel="canonical"')
            self.assertIn(f'/{slug}/', sitemap, f'/{slug}/ missing from sitemap')

    def test_unknown_content_slug_is_404_not_a_tenant_lookup(self):
        # A made-up top-level slug must 404 (or hit the hospital catch-all), never 500.
        self.assertIn(self.c.get('/no-such-marketing-page/').status_code, (404, 200, 302))


class PublicPageLinksTest(TestCase):
    """The content pages had URLs, a sitemap and JSON-LD but no link from any page
    a human lands on, so only crawlers ever reached them. The nav is built from
    CONTENT_PAGES itself, so adding a page cannot leave it unlinked."""

    def setUp(self):
        # Same reason as SeoPublicSurfaceTest: with no user in the DB,
        # SetupMiddleware sends every request to the first-run wizard.
        get_user_model().objects.create_superuser(email='owner2@x.com', password='pw')
        SetupMiddleware._configured = True
        self.client = Client()

    def test_sign_in_page_links_every_public_page(self):
        from user_mgmt.seo_views import public_pages
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        for path, label in public_pages():
            self.assertContains(r, f'href="{path}"')
            self.assertContains(r, label)

    def test_landing_footer_links_every_public_page(self):
        from user_mgmt.seo_views import public_pages
        r = self.client.get('/features/')
        self.assertEqual(r.status_code, 200)
        for path, _ in public_pages():
            self.assertContains(r, f'href="{path}"')

    def test_every_linked_page_actually_opens(self):
        """A nav entry pointing at a 404 is worse than no nav entry."""
        from user_mgmt.seo_views import public_pages
        for path, _ in public_pages():
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


class DemoBrandingLockTest(TestCase):
    """`/demo/` signs any visitor in as an ADMIN of the demo tenant, so without a
    lock one passer-by's logo and hospital name would greet everybody after them."""

    def setUp(self):
        from datetime import date, timedelta
        from saas.models import Hospital
        from saas.utils import DEMO_SLUG
        get_user_model().objects.create_superuser(email='owner3@x.com', password='pw')
        SetupMiddleware._configured = True
        exp = date.today() + timedelta(days=365)
        self.demo = Hospital.objects.create(name='Sehatyar Demo Hospital',
                                            slug=DEMO_SLUG, expiry_date=exp)
        self.real = Hospital.objects.create(name='Real Hospital', slug='real',
                                            expiry_date=exp)
        self.demo_admin = get_user_model().objects.create_user(
            email='demo@x.com', password='pw', role='ADMIN', hospital=self.demo)
        self.real_admin = get_user_model().objects.create_user(
            email='real@x.com', password='pw', role='ADMIN', hospital=self.real)

    def _settings_url(self):
        from django.urls import reverse
        return reverse('user_mgmt:site_settings')

    def _payload(self, brand):
        return {'brand_name': brand, 'brand_tagline': 't', 'logo_text': 'S',
                'primary_color': '#0891b2', 'accent_color': '#22c55e',
                'default_theme': 'light', 'print_theme': 'classic',
                'address': '', 'phone': '', 'email': '', 'license_no': '',
                'receipt_footer': '', 'mrn_prefix': 'TST', 'mrn_last_number': 0}

    def test_demo_admin_can_open_settings_but_not_save(self):
        from user_mgmt.models import SiteSettings
        c = Client()
        c.force_login(self.demo_admin)
        self.assertEqual(c.get(self._settings_url()).status_code, 200)

        c.post(self._settings_url(), self._payload('Vandalised'))
        row = SiteSettings.objects.filter(hospital=self.demo).first()
        self.assertNotEqual(getattr(row, 'brand_name', None), 'Vandalised')

    def test_a_real_hospital_admin_can_still_save(self):
        """The lock must be the demo tenant only, not settings in general."""
        from user_mgmt.models import SiteSettings
        c = Client()
        c.force_login(self.real_admin)
        c.post(self._settings_url(), self._payload('My Clinic'))
        row = SiteSettings.objects.filter(hospital=self.real).first()
        self.assertEqual(row.brand_name, 'My Clinic')
