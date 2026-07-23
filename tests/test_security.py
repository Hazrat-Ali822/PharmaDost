"""Automated security tests.

These cover the failure modes that actually matter for a multi-tenant medical
product and that have bitten this codebase before:

  * authentication  — protected pages must never serve data to anonymous users
  * tenant isolation — one hospital must never read another's records
  * fail-closed      — a user with no hospital must see nothing, not everything
                       (the historical bug: `if request.user.hospital:` before filtering)
  * authorisation    — a feature/role a user does not hold must return 403
  * CSRF             — state-changing POSTs must be rejected without a token
  * credentials      — passwords are hashed, never stored or echoed in plaintext

Static scanning (bandit) and dependency CVEs (pip-audit) are handled separately in
CI; this file covers behaviour those tools cannot see.

    python manage.py test tests.test_security --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor, Appointment
from prescriptions.models import Prescription
from inventory.models import Medicine


def _exp():
    return date.today() + timedelta(days=365)


class TwoTenantSetup(TestCase):
    """Two hospitals, each with its own patient, prescription and medicine."""

    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='Alpha Hospital', slug='alpha',
                                         expiry_date=date.today() + timedelta(days=365))
        cls.h2 = Hospital.objects.create(name='Beta Hospital', slug='beta',
                                         expiry_date=date.today() + timedelta(days=365))

        cls.admin1 = User.objects.create_user(email='a1@t.com', password='pw',
                                              role='ADMIN', hospital=cls.h1)
        cls.admin2 = User.objects.create_user(email='a2@t.com', password='pw',
                                              role='ADMIN', hospital=cls.h2)

        # NOTE: Patient.mrn is globally unique (not per-hospital), so tests must
        # supply distinct values even across different tenants.
        cls.patient1 = Patient.objects.create(full_name='Alpha Patient', gender='M',
                                              mrn='ALPHA-001', hospital=cls.h1)
        cls.patient2 = Patient.objects.create(full_name='Beta Patient', gender='F',
                                              mrn='BETA-001', hospital=cls.h2)

        # Doctor/Appointment/Prescription carry no hospital column — they are scoped
        # through the patient's hospital by the view-level helpers. That is exactly
        # what the isolation tests below verify.
        docuser2 = User.objects.create_user(email='d2@t.com', password='pw',
                                            role='DOCTOR', hospital=cls.h2)
        cls.doctor2 = Doctor.objects.create(user=docuser2, full_name='Dr Beta',
                                            opd_fee=Decimal('100'))
        appt2 = Appointment.objects.create(patient=cls.patient2, doctor=cls.doctor2)
        cls.rx2 = Prescription.objects.create(appointment=appt2, diagnosis='Beta secret')

        cls.med2 = Medicine.objects.create(name='BetaMed', price=Decimal('10'),
                                           expiry_date=_exp(), hospital=cls.h2)


class TenantIsolationTest(TwoTenantSetup):
    """Hospital Alpha must not be able to read Hospital Beta's records."""

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin1)

    def test_cannot_open_other_tenant_patient(self):
        resp = self.client.get(reverse('patient_detail', args=[self.patient2.pk]))
        self.assertIn(resp.status_code, (403, 404),
                      "Alpha admin could open Beta's patient record")

    def test_cannot_open_other_tenant_prescription(self):
        resp = self.client.get(reverse('prescription_detail', args=[self.rx2.pk]))
        self.assertIn(resp.status_code, (403, 404),
                      "Alpha admin could open Beta's prescription")

    def test_patient_list_excludes_other_tenant(self):
        resp = self.client.get(reverse('patient_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beta Patient')
        self.assertContains(resp, 'Alpha Patient')

    def test_medicine_list_excludes_other_tenant(self):
        resp = self.client.get(reverse('medicine_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'BetaMed')

    def test_pos_prescription_prefill_is_scoped(self):
        """POS accepted any ?prescription_id= at one point — a direct cross-tenant read."""
        resp = self.client.get(reverse('sale_create') + f'?prescription_id={self.rx2.pk}')
        self.assertNotContains(resp, 'Beta secret', status_code=resp.status_code)


class FailClosedTest(TwoTenantSetup):
    """A non-superuser whose hospital is None must see NOTHING.

    Regression guard for the original leak, where views asked
    `if request.user.hospital:` before filtering — so a hospital-less staff user
    fell through the filter and saw every tenant's data.
    """

    def setUp(self):
        self.orphan = User.objects.create_user(email='orphan@t.com', password='pw',
                                               role='ADMIN', hospital=None)
        self.client = Client()
        self.client.force_login(self.orphan)

    def test_hospital_less_user_sees_no_patients(self):
        resp = self.client.get(reverse('patient_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Alpha Patient')
        self.assertNotContains(resp, 'Beta Patient')

    def test_hospital_less_user_sees_no_prescriptions(self):
        resp = self.client.get(reverse('prescription_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beta Patient')

    def test_hospital_less_user_cannot_open_a_tenant_record(self):
        resp = self.client.get(reverse('prescription_detail', args=[self.rx2.pk]))
        self.assertIn(resp.status_code, (403, 404))


class AuthenticationRequiredTest(TwoTenantSetup):
    """Anonymous users get bounced to login — never served data."""

    PROTECTED = ['dashboard', 'patient_list', 'medicine_list', 'sale_create',
                 'invoice_list', 'prescription_list', 'user_mgmt:user_list',
                 'user_mgmt:site_settings', 'saas:dashboard', 'audit_log']

    def test_anonymous_is_redirected_to_login(self):
        client = Client()
        for name in self.PROTECTED:
            with self.subTest(page=name):
                resp = client.get(reverse(name))
                self.assertEqual(resp.status_code, 302,
                                 f"{name} did not redirect an anonymous user")
                self.assertIn('/login', resp['Location'])

    def test_anonymous_detail_view_leaks_nothing(self):
        resp = Client().get(reverse('patient_detail', args=[self.patient2.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertNotContains(resp, 'Beta Patient', status_code=302)


class AuthorisationTest(TwoTenantSetup):
    """Holding a login is not holding a permission."""

    def test_non_admin_cannot_reach_user_management(self):
        pharmacist = User.objects.create_user(email='ph@t.com', password='pw',
                                              role='PHARMACIST', hospital=self.h1)
        c = Client(); c.force_login(pharmacist)
        self.assertEqual(c.get(reverse('user_mgmt:user_list')).status_code, 403)
        self.assertEqual(c.get(reverse('user_mgmt:site_settings')).status_code, 403)

    def test_non_superuser_cannot_reach_saas_portal(self):
        """The SaaS portal exposes every tenant — admins of a hospital must not enter."""
        c = Client(); c.force_login(self.admin1)
        resp = c.get(reverse('saas:dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])

    def test_revoked_feature_returns_403(self):
        """custom_features is an exact allow-list; removing a key must lock the view."""
        pharmacist = User.objects.create_user(email='ph2@t.com', password='pw',
                                              role='PHARMACIST', hospital=self.h1)
        pharmacist.custom_features = ['inventory']      # 'pos' deliberately withheld
        pharmacist.save()
        c = Client(); c.force_login(pharmacist)
        self.assertEqual(c.get(reverse('sale_create')).status_code, 403)
        self.assertEqual(c.get(reverse('medicine_list')).status_code, 200)

    def test_nurse_cannot_reach_billing_or_pharmacy(self):
        nurse = User.objects.create_user(email='nu@t.com', password='pw',
                                         role='NURSE', hospital=self.h1)
        c = Client(); c.force_login(nurse)
        self.assertEqual(c.get(reverse('sale_create')).status_code, 403)
        self.assertEqual(c.get(reverse('invoice_list')).status_code, 403)


class CsrfTest(TwoTenantSetup):
    """State-changing POSTs must carry a CSRF token."""

    def test_post_without_csrf_token_is_rejected(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin1)
        resp = c.post(reverse('patient_add'), {'full_name': 'Injected', 'gender': 'M'})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Patient.objects.filter(full_name='Injected').exists())


class CredentialTest(TestCase):
    """Passwords are hashed and never round-trip in plaintext."""

    def test_password_is_hashed(self):
        u = User.objects.create_user(email='pw@t.com', password='sup3r-s3cret')
        self.assertNotEqual(u.password, 'sup3r-s3cret')
        self.assertTrue(u.password.startswith(('pbkdf2_', 'argon2', 'bcrypt', 'md5$')))
        self.assertTrue(u.check_password('sup3r-s3cret'))

    def test_login_page_does_not_echo_password(self):
        c = Client()
        resp = c.post(reverse('login'), {'username': 'pw@t.com', 'password': 'wrong-guess'})
        self.assertNotContains(resp, 'wrong-guess', status_code=resp.status_code)


class SecretKeyGuardTest(TestCase):
    """The key signs session cookies and password-reset tokens. A server running
    on the published default is one anyone can forge a login into."""

    SETTINGS = Path(__file__).resolve().parent.parent / 'pharma_mgmt' / 'settings.py'

    def _source(self):
        return self.SETTINGS.read_text(encoding='utf-8')

    def test_a_server_refuses_to_start_on_the_default_key(self):
        source = self._source()
        self.assertIn('_looks_like_a_server and SECRET_KEY == _INSECURE_SECRET_KEY', source,
                      'the guard must key on the server signal, not an env var nobody sets')
        self.assertIn('raise RuntimeError', source)

    def test_the_guard_does_not_depend_on_DJANGO_ENV(self):
        """It used to, and nothing sets DJANGO_ENV on the PythonAnywhere host —
        so the check could never fire where it mattered. (The name still appears
        in a comment explaining why; what must not come back is reading it.)"""
        self.assertNotIn('getenv("DJANGO_ENV")', self._source())

    def test_local_development_still_runs_without_a_key(self):
        from django.conf import settings as live
        self.assertTrue(live.SECRET_KEY, 'the suite itself must not need a key set')
