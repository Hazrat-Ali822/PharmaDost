"""Back must not show pre-edit data.

The report that produced this: a test was cancelled, the amount on the previous
screen still read the old total, and only a manual refresh fixed it. Nothing was
wrong server-side — the browser had restored that screen from its bfcache and
never asked. `user_mgmt.middleware.DataVersionMiddleware` gives the client a way
to notice: a `dv` cookie that changes on every write, against the token rendered
into `<body data-dv>` when the page was built.

What matters here is both halves. A write MUST change the token (or Back stays
stale, the bug). A read MUST NOT (or every Back tap costs a round trip on a clinic
connection, which is the cure being worse than the disease).

    python manage.py test tests.test_freshness --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from user_mgmt.middleware import DATA_VERSION_COOKIE as DV


class DataVersionTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='Fresh Hospital', slug='fresh',
                                         expiry_date=date.today() + timedelta(days=365))
        self.admin = User.objects.create_user(email='fresh@h.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.admin)

    def _dv(self):
        c = self.client.cookies.get(DV)
        return c.value if c else None

    def test_first_page_carries_the_same_token_it_hands_the_browser(self):
        """Or the very first screen decides it is already stale and reloads itself."""
        r = self.client.get(reverse('patient_list'))
        self.assertEqual(r.status_code, 200)
        token = self._dv()
        self.assertTrue(token)
        self.assertContains(r, f'data-dv="{token}"')

    def test_a_read_does_not_change_the_token(self):
        self.client.get(reverse('patient_list'))
        before = self._dv()
        self.client.get(reverse('patient_list'))
        self.client.get(reverse('dashboard_page'))
        self.assertEqual(self._dv(), before)

    def test_a_write_changes_the_token(self):
        self.client.get(reverse('patient_list'))
        before = self._dv()

        r = self.client.post(reverse('patient_add'), {
            'full_name': 'New Patient', 'phone': '03001234567', 'gender': 'M'})
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(Patient.objects.filter(full_name='New Patient').exists())
        self.assertNotEqual(self._dv(), before)

    def test_the_page_after_a_write_carries_the_new_token(self):
        """The whole mechanism rests on this: the screen you land on after saving
        must agree with the cookie, or it immediately re-fetches itself."""
        self.client.get(reverse('patient_list'))
        self.client.post(reverse('patient_add'), {
            'full_name': 'Another Patient', 'phone': '03007654321', 'gender': 'F'})
        r = self.client.get(reverse('patient_list'))
        self.assertContains(r, f'data-dv="{self._dv()}"')

    def test_marking_notifications_read_does_not_change_the_token(self):
        """A bell being cleared changes nothing any page displays; bumping for it
        would make Back re-fetch after every notification poll."""
        self.client.get(reverse('patient_list'))
        before = self._dv()
        self.client.post(reverse('accounts:mark_notifications_read'))
        self.assertEqual(self._dv(), before)

    def test_a_rejected_form_still_bumps_the_token_deliberately(self):
        """It wrote nothing, so this is a false positive — and the right one.

        Telling a successful POST-that-renders from a POST-that-failed needs a
        guess, and guessing the other way brings the original bug back. The cost
        of guessing this way is one extra fetch on the next Back; the cost of
        guessing the other way is a bill on screen that is no longer true."""
        self.client.get(reverse('patient_list'))
        before = self._dv()
        r = self.client.post(reverse('patient_add'), {'full_name': ''})
        self.assertEqual(r.status_code, 200)          # re-rendered with errors
        self.assertNotEqual(self._dv(), before)

    def test_a_form_rerendered_after_a_failed_post_carries_no_token(self):
        """`data-dv` is omitted on non-GET renders on purpose: the page must never
        decide to re-fetch itself and throw the user's typing away."""
        r = self.client.post(reverse('patient_add'), {'full_name': ''})
        self.assertNotContains(r, 'data-dv=')

    def test_every_page_has_a_back_button(self):
        r = self.client.get(reverse('patient_list'))
        self.assertContains(r, 'id="appBack"')
