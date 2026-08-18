"""An uploaded logo is shrunk, bounded, and never honoured on the public demo.

The bug these carry: `/demo/` signs every visitor in as an ADMIN of the demo
tenant, so before the settings screen was locked a passer-by could set the demo
hospital's logo for everyone who came afterwards. One did — a 1 MB, 3369x4160
photograph of a pair of trainers. The lock that followed refused the *next*
write and left the stored one alone, and it was only invisible because `/media/`
was returning 404 at the time; the moment media was actually served, the demo's
sign-in page showed the trainers.

Two separate holes, so two separate guards:

* nothing bounded or shrank a logo upload for *any* tenant, and
* the demo's lock was write-side only.

    python manage.py test user_mgmt.tests_branding_logo --settings=pharma_mgmt.test_settings
"""
import io
import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital
from user_mgmt.branding_images import (LEAVE_ALONE_BYTES, MAX_LOGO_BYTES,
                                       MAX_LOGO_EDGE, compress_logo)
from user_mgmt.models import SiteSettings
from user_mgmt.site_forms import SiteSettingsForm

MEDIA = Path(tempfile.mkdtemp())


def _photo(size=(3369, 4160), mode='RGB', name='IMG_1000184909.jpg', fmt='JPEG'):
    """A picture that is not a logo — the shape of the thing that got uploaded."""
    from PIL import Image
    img = Image.new(mode, size)
    px = img.load()
    # Noise, so JPEG cannot compress it down to nothing and the size is realistic.
    for x in range(0, size[0], 7):
        for y in range(0, size[1], 7):
            px[x, y] = ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)[:len(img.getbands())]
    buf = io.BytesIO()
    img.save(buf, fmt)
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(),
                              content_type='image/jpeg' if fmt == 'JPEG' else 'image/png')


def _tiny_png(name='logo.png', size=(64, 64)):
    """A small, hand-made-looking logo — the kind that must be left alone."""
    from PIL import Image
    img = Image.new('RGBA', size, (220, 38, 38, 255))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='image/png')


def _big_transparent_png(name='logo.png', size=(700, 700)):
    """Big enough to be processed, with a large genuinely transparent region.

    Noise, because a flat colour PNG compresses to a few KB however many pixels
    it has — and then the compressor rightly leaves it alone and the test proves
    nothing.
    """
    from PIL import Image
    w, h = size
    # os.urandom, not a repeating pattern: PNG squeezes anything periodic
    # down to a few KB and the compressor then rightly leaves it alone.
    raw = os.urandom(w * h * 4)
    img = Image.frombytes('RGBA', size, raw)
    img.paste((0, 0, 0, 0), (0, 0, w // 2, h))              # transparent left half
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA)
class LogoCompressionTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_a_camera_photograph_is_shrunk_to_a_logo(self):
        from PIL import Image
        original = _photo()
        self.assertGreater(original.size, LEAVE_ALONE_BYTES)

        out = compress_logo(original)

        self.assertLess(out.size, original.size)
        out.seek(0)
        img = Image.open(out)
        self.assertLessEqual(max(img.size), MAX_LOGO_EDGE)

    def test_transparency_survives(self):
        """A logo sits on the dark sidebar and on a white letterhead. Flattening
        the alpha onto white puts a white block behind it in the sidebar — the
        exact defect the default mark had to be regenerated to fix."""
        from PIL import Image
        big = _big_transparent_png()
        self.assertGreater(big.size, LEAVE_ALONE_BYTES,
                           'this fixture must be large enough to be processed')

        out = compress_logo(big)
        out.seek(0)
        img = Image.open(out)
        self.assertIn(img.mode, ('RGBA', 'LA', 'P'))
        self.assertEqual(img.convert('RGBA').getpixel((2, 2))[3], 0)

    def test_a_small_logo_is_returned_untouched(self):
        """Re-encoding a hand-made PNG gains nothing and can only lose."""
        small = _tiny_png()
        self.assertLess(small.size, LEAVE_ALONE_BYTES)
        self.assertIs(compress_logo(small), small)

    def test_the_handle_is_rewound_for_the_colour_picker(self):
        """The settings view reads the same upload again to pick the theme
        colour out of it. A handle left at EOF turns that into a silent
        'could not read a colour from the logo'."""
        from user_mgmt.color_utils import dominant_color
        big = _big_transparent_png()
        compress_logo(big)
        self.assertEqual(big.tell(), 0)
        self.assertIsNotNone(dominant_color(big))

    def test_something_pillow_cannot_read_is_kept_rather_than_lost(self):
        junk = SimpleUploadedFile('logo.png', b'x' * (LEAVE_ALONE_BYTES + 10),
                                  content_type='image/png')
        self.assertIs(compress_logo(junk), junk)


@override_settings(MEDIA_ROOT=MEDIA)
class LogoUploadLimitTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _form(self, logo):
        return SiteSettingsForm(
            {'brand_name': 'X', 'logo_text': 'X', 'print_theme': 'classic',
             'default_theme': 'light', 'primary_color': '#4f46e5',
             'accent_color': '#4338ca', 'mrn_last_number': '0'},
            {'logo_image': logo})

    def test_an_enormous_file_is_refused_with_a_readable_message(self):
        """The ceiling is patched down rather than generating a real 4 MB
        picture. Note the fixture has to be a *valid* image: Django's own
        ImageField validation runs before this and would reject junk bytes with
        its own message, which would make the test pass for the wrong reason."""
        from unittest.mock import patch
        big = _photo(size=(1200, 1200))
        with patch('user_mgmt.branding_images.MAX_LOGO_BYTES', big.size - 1):
            form = self._form(big)
            self.assertFalse(form.is_valid())
        self.assertIn('logo_image', form.errors)
        self.assertIn('photograph', form.errors['logo_image'][0])

    def test_the_ceiling_is_a_real_number_not_a_placeholder(self):
        self.assertGreaterEqual(MAX_LOGO_BYTES, 1024 * 1024)

    def test_a_normal_upload_is_accepted_and_shrunk(self):
        from PIL import Image
        form = self._form(_photo(size=(2000, 2000)))
        self.assertTrue(form.is_valid(), form.errors)
        img = Image.open(form.cleaned_data['logo_image'])
        self.assertLessEqual(max(img.size), MAX_LOGO_EDGE)

    def test_leaving_the_field_alone_does_not_rewrite_a_stored_logo(self):
        """An unchanged ImageField hands back the existing FieldFile. Running it
        through the compressor would re-save the logo every time an unrelated
        setting was posted."""
        clear_current_hospital()
        site = SiteSettings.load()
        site.logo_image = 'branding/kept.png'
        site.save(update_fields=['logo_image'])

        form = SiteSettingsForm(
            {'brand_name': 'X', 'logo_text': 'X', 'print_theme': 'classic',
             'default_theme': 'light', 'primary_color': '#4f46e5',
             'accent_color': '#4338ca', 'mrn_last_number': '0'},
            instance=site)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['logo_image'].name, 'branding/kept.png')


class DemoLogoIsNeverHonouredTest(TestCase):
    """The write lock could not undo a write that had already happened."""

    def setUp(self):
        self.demo = Hospital.objects.create(
            name='Sehatyar Demo Hospital', slug='demo',
            expiry_date=date.today() + timedelta(days=3650))
        self.real = Hospital.objects.create(
            name='Real Hospital', slug='real-h',
            expiry_date=date.today() + timedelta(days=365))
        # Without a single user the app is in first-run state and every login
        # route redirects to the setup wizard, so the page under test never renders.
        User.objects.create_user(email='staff@real.com', password='pw',
                                 role='ADMIN', hospital=self.real)

    def tearDown(self):
        clear_current_hospital()

    def _set_logo(self, hospital, name='branding/trainers.jpg'):
        set_current_hospital(hospital)
        row = SiteSettings.load()
        SiteSettings.objects.filter(pk=row.pk).update(logo_image=name)
        clear_current_hospital()
        return row.pk

    def test_a_logo_already_in_the_demos_row_is_ignored_on_read(self):
        self._set_logo(self.demo)
        set_current_hospital(self.demo)
        self.assertFalse(SiteSettings.load().logo_image)

    def test_a_real_tenants_logo_is_still_honoured(self):
        """The guard must key on the demo slug, not on 'has a logo'."""
        self._set_logo(self.real, 'branding/real-logo.png')
        set_current_hospital(self.real)
        self.assertEqual(SiteSettings.load().logo_image.name, 'branding/real-logo.png')

    def test_the_demo_sign_in_page_does_not_show_the_uploaded_picture(self):
        """The page the complaint was actually about — sehatyar.online/demo/login/
        was serving a photograph of a pair of trainers as the hospital's logo."""
        self._set_logo(self.demo)
        html = self.client.get('/demo/login/').content.decode()
        self.assertNotIn('trainers.jpg', html)
        self.assertNotIn('/media/branding/', html)
        # falls through to the lettered mark this template uses with no logo
        self.assertIn('mobile-logo-fallback', html)

    def test_a_real_tenants_sign_in_page_still_shows_its_logo(self):
        self._set_logo(self.real, 'branding/real-logo.png')
        html = self.client.get('/real-h/login/').content.decode()
        self.assertIn('real-logo.png', html)

    def test_saving_settings_on_the_demo_is_still_refused(self):
        """The write lock stays — this guard is a second line, not a swap."""
        User.objects.create_user(email='demo@sehatyar.online', password='pw',
                                 role='ADMIN', hospital=self.demo)
        self.client.login(email='demo@sehatyar.online', password='pw')
        self.client.post('/manage/settings/', {
            'brand_name': 'Hijacked', 'logo_text': 'H', 'print_theme': 'classic',
            'default_theme': 'light', 'primary_color': '#000000',
            'accent_color': '#000000', 'mrn_last_number': '0'})
        set_current_hospital(self.demo)
        self.assertNotEqual(SiteSettings.load().brand_name, 'Hijacked')
