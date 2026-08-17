"""Uploaded files are served, and patient documents are not served here.

The bug this carries: `pharma_mgmt/urls.py` served media with
`django.conf.urls.static.static()`, which **returns an empty list when DEBUG is
False**. On the deployed host DEBUG is off, so `/media/` 404'd — and because
every logo `<img>` carries an `onerror` fallback to the default mark, each
tenant's uploaded logo simply showed somebody else's brand and nobody reported
it. Fetching a real uploaded logo on the live site returned 404, which is how it
was found.

Django's test runner sets `DEBUG = False`, so these tests exercise exactly the
configuration that was broken.

    python manage.py test tests.test_media --settings=pharma_mgmt.test_settings
"""
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.test import Client, TestCase, override_settings

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital

MEDIA = Path(tempfile.mkdtemp())


@override_settings(MEDIA_ROOT=MEDIA)
class MediaServingTest(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (MEDIA / 'branding').mkdir(parents=True, exist_ok=True)
        (MEDIA / 'branding' / 'logo.jpg').write_bytes(b'\xff\xd8\xff\xe0 fake jpeg')
        (MEDIA / 'patient_docs' / '1').mkdir(parents=True, exist_ok=True)
        (MEDIA / 'patient_docs' / '1' / 'secret.jpg').write_bytes(b'\xff\xd8 private')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-media',
                                         expiry_date=date.today() + timedelta(days=365))
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def test_an_uploaded_logo_is_served_with_debug_off(self):
        """The whole bug. `static()` is a no-op when DEBUG is False, so this
        404'd on the live site."""
        from django.conf import settings
        self.assertFalse(settings.DEBUG, 'the test settings must have DEBUG off')

        r = Client().get('/media/branding/logo.jpg')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(b''.join(r.streaming_content).startswith(b'\xff\xd8'))

    def test_the_logo_is_reachable_without_signing_in(self):
        """It is on the sign-in page itself, before anyone has a session."""
        r = Client().get('/media/branding/logo.jpg')
        self.assertEqual(r.status_code, 200)

    def test_a_patient_document_is_not_reachable_through_media(self):
        """It shares MEDIA_ROOT — one upload tree, one folder to back up — so
        the public door has to refuse it. Served by patients.views.document_file
        instead, behind the login and the tenant scope."""
        c = Client(); c.login(email='a@a.com', password='pw')
        self.assertEqual(c.get('/media/patient_docs/1/secret.jpg').status_code, 404)

    def test_it_is_refused_anonymously_too(self):
        self.assertEqual(Client().get('/media/patient_docs/1/secret.jpg').status_code, 404)

    def test_the_refusal_is_not_defeated_by_capitalisation(self):
        """A case-sensitive startswith is no guard on a case-insensitive
        filesystem, which is where this is developed."""
        c = Client(); c.login(email='a@a.com', password='pw')
        for path in ('/media/Patient_Docs/1/secret.jpg',
                     '/media/PATIENT_DOCS/1/secret.jpg'):
            self.assertEqual(c.get(path).status_code, 404, path)

    def test_walking_out_of_the_media_root_is_refused(self):
        r = Client().get('/media/../settings.py')
        self.assertNotEqual(r.status_code, 200)

    def test_a_missing_file_is_a_404(self):
        self.assertEqual(Client().get('/media/branding/nope.jpg').status_code, 404)

    def test_public_uploads_are_cacheable(self):
        """Without this the logo is re-fetched on every page of every visit."""
        r = Client().get('/media/branding/logo.jpg')
        self.assertIn('max-age', r['Cache-Control'])
