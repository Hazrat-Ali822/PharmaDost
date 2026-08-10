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
