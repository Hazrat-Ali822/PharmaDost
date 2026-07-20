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
