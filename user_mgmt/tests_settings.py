"""SiteSettings singleton + per-hospital creation must not collide on the primary key."""
from datetime import date, timedelta

from django.test import TestCase

from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital
from user_mgmt.models import SiteSettings


class SiteSettingsLoadTest(TestCase):
    def tearDown(self):
        clear_current_hospital()

    def _hospital(self, name, slug):
        return Hospital.objects.create(
            name=name, slug=slug, expiry_date=date.today() + timedelta(days=30))

    def test_each_hospital_gets_its_own_row_without_collision(self):
        a = self._hospital('Hosp A', 'a')
        b = self._hospital('Hosp B', 'b')

        set_current_hospital(a)
        sa = SiteSettings.load()
        set_current_hospital(b)
        sb = SiteSettings.load()

        self.assertNotEqual(sa.id, sb.id)
        self.assertEqual(sa.hospital_id, a.id)
        self.assertEqual(sb.hospital_id, b.id)
        # loading again returns the same rows, not new ones
        set_current_hospital(a)
        self.assertEqual(SiteSettings.load().id, sa.id)

    def test_global_settings_is_a_singleton(self):
        clear_current_hospital()
        g1 = SiteSettings.load()
        g2 = SiteSettings.load()
        self.assertEqual(g1.id, g2.id)
        self.assertIsNone(g1.hospital_id)
        self.assertEqual(SiteSettings.objects.filter(hospital__isnull=True).count(), 1)


class LogoColourTest(TestCase):
    def test_dominant_colour_is_the_vivid_mark_not_the_white_background(self):
        import io
        from PIL import Image
        from user_mgmt.color_utils import dominant_color, darker
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        for x in range(30, 70):
            for y in range(30, 70):
                img.putpixel((x, y), (13, 148, 136, 255))   # a teal mark
        buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
        colour = dominant_color(buf)
        # a green-dominant teal, not white
        self.assertIsNotNone(colour)
        r, g, b = int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
        self.assertGreater(g, 100)
        self.assertLess(r, 100)
        # accent is a darker shade
        self.assertNotEqual(darker(colour), colour)

    def test_blank_white_logo_yields_no_colour(self):
        import io
        from PIL import Image
        from user_mgmt.color_utils import dominant_color
        img = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
        buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
        self.assertIsNone(dominant_color(buf))

    def test_settings_save_derives_colour_from_uploaded_logo(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.models import User

        h = Hospital.objects.create(name='ColourCo', slug='cc',
                                    expiry_date=date.today() + timedelta(days=30))
        User.objects.create_user(email='admin@cc.com', password='pw', role='ADMIN', hospital=h)
        img = Image.new("RGBA", (60, 60), (255, 255, 255, 255))
        for x in range(60):
            for y in range(60):
                img.putpixel((x, y), (220, 38, 38, 255))    # solid red
        buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
        logo = SimpleUploadedFile('logo.png', buf.read(), content_type='image/png')

        self.client.login(email='admin@cc.com', password='pw')
        self.client.post('/manage/settings/', {
            'brand_name': 'ColourCo', 'brand_tagline': '', 'logo_text': 'C',
            'primary_color': '#4f46e5', 'accent_color': '#4338ca',
            'address': '', 'phone': '', 'email': '', 'license_no': '',
            'receipt_footer': '', 'print_theme': 'classic',
            'mrn_prefix': '', 'mrn_last_number': '0',
            'logo_image': logo, 'color_from_logo': '1',
        })
        set_current_hospital(h)
        s = SiteSettings.load()
        # the theme is now red-dominant, not the indigo we posted
        self.assertGreater(int(s.primary_color[1:3], 16), 150)   # red channel high
        self.assertLess(int(s.primary_color[3:5], 16), 120)      # green low
