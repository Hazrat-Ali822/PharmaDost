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


class TemplateCommentTest(TestCase):
    """Reported: a developer's note was printed on every lab report given to a
    patient — under the RESULTS heading, in body text, on a clinical document.

    `{# … #}` is a **single-line** comment in Django. Spanning one over two lines
    does not comment them out: the opener is not closed, so Django renders the
    whole thing as literal text (and evaluates any `{{ }}` inside it). CLAUDE.md
    has warned about this for a while and it still shipped twice, so this scans
    the whole template tree rather than guarding the one file that was caught.
    """

    def test_no_template_opens_a_hash_comment_it_does_not_close(self):
        import glob
        import io
        import os
        from django.conf import settings

        roots = [os.path.join(settings.BASE_DIR, 'templates')]
        roots += glob.glob(os.path.join(settings.BASE_DIR, '*', 'templates'))
        offenders = []
        for root in roots:
            for path in glob.glob(os.path.join(root, '**', '*.html'), recursive=True):
                for n, line in enumerate(
                        io.open(path, encoding='utf-8', errors='replace'), start=1):
                    idx = line.find('{#')
                    if idx != -1 and '#}' not in line[idx:]:
                        rel = os.path.relpath(path, settings.BASE_DIR)
                        offenders.append(f'{rel}:{n}: {line.strip()[:80]}')
        self.assertEqual(
            offenders, [],
            'These open a {# comment and never close it on the same line, so the '
            'text is PRINTED on the page. Use {% comment %}…{% endcomment %}:\n  '
            + '\n  '.join(offenders))


class DoctorTitleTest(TestCase):
    """Reported: "Dr. Dr. Sara Ahmed" on nine screens, two of them documents given
    to the patient (the OPD token slip and the IPD discharge summary).

    The name is typed as "Dr. Sara Ahmed" and ~34 templates prefix "Dr." of their
    own. Editing 34 templates is 34 chances to miss one, so the single stored value
    is normalised instead and `display_name` is the one place a title is added.
    """

    def test_a_typed_title_is_stripped_on_save(self):
        from opd.models import Doctor
        for typed, expected in (('Dr. Sara Ahmed', 'Sara Ahmed'),
                                ('dr Imran Khan', 'Imran Khan'),
                                ('Doctor Ayesha', 'Ayesha'),
                                ('Prof. Bilal', 'Bilal'),
                                ('Sara Ahmed', 'Sara Ahmed')):
            with self.subTest(typed=typed):
                d = Doctor.objects.create(full_name=typed)
                self.assertEqual(d.full_name, expected)

    def test_a_name_that_merely_starts_with_those_letters_is_left_alone(self):
        """'Drew' begins with 'dr' — stripping on a prefix match rather than a
        whole word would turn him into 'ew'."""
        from opd.models import Doctor
        self.assertEqual(Doctor.objects.create(full_name='Drew Mansoor').full_name,
                         'Drew Mansoor')

    def test_display_name_adds_exactly_one_title(self):
        from opd.models import Doctor
        d = Doctor.objects.create(full_name='Dr. Sara Ahmed')
        self.assertEqual(d.display_name, 'Dr. Sara Ahmed')
        self.assertEqual(str(d), 'Dr. Sara Ahmed')


class InvoicePrintingTest(TestCase):
    """Reported: every other document prints — token slip, pharmacy receipt, lab
    and imaging reports, discharge summary, whole-patient bill — but a single
    OPD / lab / IPD invoice had no print route, so the one thing a patient asks
    for at the counter could not be handed over. Payment was all-or-nothing too."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from billing.models import Invoice
        from patients.models import Patient
        cls.h = Hospital.objects.create(name='Bill H', slug='bill-h',
                                        expiry_date=date.today() + timedelta(days=365))
        cls.admin = User.objects.create_user(email='bill@t.com', password='pw',
                                             role='ADMIN', hospital=cls.h)
        cls.patient = Patient.objects.create(full_name='Bill Patient', gender='M',
                                             hospital=cls.h)
        cls.invoice = Invoice.objects.create(patient=cls.patient, hospital=cls.h,
                                             total=Decimal('1500'),
                                             created_by=cls.admin)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def test_an_invoice_can_be_printed(self):
        resp = self.client.get(reverse('invoice_print', args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.invoice.display_no)
        self.assertContains(resp, 'Bill Patient')

    def test_the_detail_page_offers_the_print_link(self):
        resp = self.client.get(reverse('invoice_detail', args=[self.invoice.pk]))
        self.assertContains(resp, reverse('invoice_print', args=[self.invoice.pk]))

    def test_a_part_payment_can_be_recorded(self):
        from decimal import Decimal
        self.client.post(reverse('invoice_mark_paid', args=[self.invoice.pk]),
                         {'amount': '500', 'payment_method': 'CASH'})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('500.00'))
        self.assertEqual(self.invoice.balance, Decimal('1000.00'))
        self.assertFalse(self.invoice.is_paid)

    def test_a_second_part_payment_adds_up(self):
        from decimal import Decimal
        for amount in ('500', '1000'):
            self.client.post(reverse('invoice_mark_paid', args=[self.invoice.pk]),
                             {'amount': amount, 'payment_method': 'CASH'})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('1500.00'))
        self.assertTrue(self.invoice.is_paid)

    def test_a_blank_amount_still_means_the_whole_balance(self):
        """What the old single button did — it must keep working."""
        from decimal import Decimal
        self.client.post(reverse('invoice_mark_paid', args=[self.invoice.pk]),
                         {'amount': '', 'payment_method': 'CASH'})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('1500.00'))

    def test_overpaying_never_pushes_the_balance_negative(self):
        from decimal import Decimal
        self.client.post(reverse('invoice_mark_paid', args=[self.invoice.pk]),
                         {'amount': '99999', 'payment_method': 'CASH'})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('1500.00'))
        self.assertEqual(self.invoice.balance, Decimal('0.00'))
