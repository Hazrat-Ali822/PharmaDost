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

    def test_patient_index_json_is_tenant_scoped(self):
        """The offline patient index (whole registry as JSON) must scope exactly
        like the list — Alpha's admin sees Alpha's patients, never Beta's."""
        resp = self.client.get(reverse('patient_index'))
        self.assertEqual(resp.status_code, 200)
        names = [p['name'] for p in resp.json()['patients']]
        self.assertIn('Alpha Patient', names)
        self.assertNotIn('Beta Patient', names)

    def test_pos_prescription_prefill_is_scoped(self):
        """POS accepted any ?prescription_id= at one point — a direct cross-tenant read."""
        resp = self.client.get(reverse('sale_create') + f'?prescription_id={self.rx2.pk}')
        self.assertNotContains(resp, 'Beta secret', status_code=resp.status_code)

    def test_payout_screens_are_scoped(self):
        """`Doctor` has no `hospital` column, so `TenantManager` cannot protect it —
        every view that lists or fetches one must narrow the queryset itself
        (`opd.scoping.scoped_doctors`). The payout screens did not, and queried
        `Doctor.objects.all()`: the list named every tenant's doctors and the
        detail page opened their earnings."""
        resp = self.client.get(reverse('payout_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Dr Beta')

        detail = self.client.get(reverse('payout_doctor', args=[self.doctor2.pk]))
        self.assertIn(detail.status_code, (403, 404),
                      "Alpha admin could open Beta's doctor payout page")

    def test_alpha_cannot_record_a_payout_against_betas_doctor(self):
        """The payout page also POSTs money. An unscoped `get_object_or_404` made
        that a cross-tenant *write*, not just a read."""
        from opd.models import DoctorPayout
        before = DoctorPayout.objects.filter(doctor=self.doctor2).count()
        self.client.post(reverse('payout_doctor', args=[self.doctor2.pk]), {
            'date': date.today().isoformat(), 'amount': '5000',
            'payment_method': 'CASH', 'note': 'from Alpha'})
        self.assertEqual(DoctorPayout.objects.filter(doctor=self.doctor2).count(),
                         before, 'Alpha wrote a payout against Beta doctor')


class SharedCatalogueTest(TwoTenantSetup):
    """The lab and imaging price lists are each hospital's own.

    They used to be one platform-wide table — no `hospital` column, a plain
    manager, and a bulk-save loop over `LabTest.objects.all()`. So any tenant's
    admin pressing Save on `/lab/tests/` rewrote *every other tenant's* prices,
    could inject tests into their menus, and (on `/imaging/scans/`, which deletes
    by pk) delete their scans outright. Those prices build the patient's invoice,
    so it set what other hospitals charge.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from lab.models import LabTest, TestCategory
        from imaging.models import ScanType
        cls.cat2 = TestCategory.all_objects.create(name='Beta Cat', hospital=cls.h2)
        cls.test2 = LabTest.all_objects.create(name='Beta CBC', category=cls.cat2,
                                               price=Decimal('300'), hospital=cls.h2)
        cls.scan2 = ScanType.all_objects.create(name='Beta MRI', modality='MRI',
                                                price=Decimal('8000'), hospital=cls.h2)
        # An Alpha appointment, so the prescription screen (which offers the lab
        # and scan catalogues) can be opened as Alpha.
        docuser1 = User.objects.create_user(email='d1@t.com', password='pw',
                                            role='DOCTOR', hospital=cls.h1)
        doctor1 = Doctor.objects.create(user=docuser1, full_name='Dr Alpha',
                                        opd_fee=Decimal('100'))
        cls.appt1 = Appointment.objects.create(patient=cls.patient1, doctor=doctor1)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin1)      # ALPHA

    def test_the_catalogue_screens_show_only_your_own(self):
        self.assertNotContains(self.client.get(reverse('lab:test_catalog')),
                               'Beta CBC')
        self.assertNotContains(self.client.get(reverse('imaging:scan_catalog')),
                               'Beta MRI')

    def test_alpha_cannot_reprice_betas_lab_test(self):
        from lab.models import LabTest
        self.client.post(reverse('lab:test_catalog'), {f'price_{self.test2.pk}': '9999'})
        self.test2.refresh_from_db()
        self.assertEqual(self.test2.price, Decimal('300.00'),
                         "Alpha rewrote Beta's lab price")
        # …and the row is still Beta's, not silently re-stamped.
        self.assertEqual(LabTest.all_objects.get(pk=self.test2.pk).hospital, self.h2)

    def test_alphas_new_test_does_not_appear_in_betas_menu(self):
        from lab.models import TestCategory
        cat1 = TestCategory.all_objects.create(name='Alpha Cat', hospital=self.h1)
        self.client.post(reverse('lab:test_catalog'), {
            'add': '1', 'name': 'ALPHA-INJECTED', 'category': cat1.pk, 'price': '55'})
        beta = Client(); beta.force_login(self.admin2)
        self.assertNotContains(beta.get(reverse('lab:test_catalog')), 'ALPHA-INJECTED')

    def test_alpha_cannot_reprice_or_delete_betas_scan(self):
        from imaging.models import ScanType
        self.client.post(reverse('imaging:scan_catalog'),
                         {f'price_{self.scan2.pk}': '1', f'active_{self.scan2.pk}': 'on'})
        self.scan2.refresh_from_db()
        self.assertEqual(self.scan2.price, Decimal('8000.00'),
                         "Alpha rewrote Beta's scan price")

        self.client.post(reverse('imaging:scan_catalog'), {'delete': str(self.scan2.pk)})
        self.assertTrue(ScanType.all_objects.filter(pk=self.scan2.pk).exists(),
                        "Alpha deleted Beta's scan type")

    def test_the_ordering_screens_offer_only_your_own_catalogue(self):
        """A form field's queryset must be built in `__init__`, never at class level.

        `queryset=LabTest.objects.all()` written as a class attribute is evaluated
        **once, at import**, when no tenant is bound to the thread — so
        `TenantManager` hands back every row and that unfiltered queryset is then
        reused for the life of the process. Once each hospital had its own
        catalogue, the lab-order and prescription screens listed every tenant's
        tests and scans. Worse than a display bug: `ModelMultipleChoiceField`
        validates submitted ids against its own queryset, so the POST would have
        been accepted too.
        """
        for url in (reverse('lab:order_create'),
                    reverse('prescription_create', args=[self.appt1.pk])):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn('Beta CBC', body)
                self.assertNotIn('Beta MRI', body)

    def test_alpha_cannot_order_betas_lab_test_by_posting_its_id(self):
        """The display fix and the validation fix are the same fix — prove it.

        Driven through the view, not by building the form directly: the scoping is
        `TenantManager` reading the thread-local, and only `TenantMiddleware` binds
        that. A form instantiated in a bare unit test has no tenant bound and is
        unfiltered by design, so testing it that way would prove nothing.
        """
        from lab.models import TestOrder
        before = TestOrder.objects.count()
        resp = self.client.post(reverse('lab:order_create'),
                                {'patient': self.patient1.pk, 'tests': [self.test2.pk]})
        self.assertEqual(TestOrder.objects.count(), before,
                         "Alpha ordered Beta's lab test by posting its id")
        self.assertContains(resp, 'not one of the available choices', status_code=200)

    def test_a_hospital_less_install_still_has_a_catalogue(self):
        """The desktop / LAN build has no tenant at all: its admin is a
        hospital-less superuser and its rows carry `hospital = NULL`. The scoping
        must not lock that install out of its own price list — which is what a
        blanket `hospital=request.user.hospital` filter would do if it keyed on
        "has a hospital" rather than on the value."""
        from lab.models import LabTest, TestCategory
        cat = TestCategory.all_objects.create(name='Local Cat', hospital=None)
        LabTest.all_objects.create(name='Local CBC', category=cat,
                                   price=Decimal('100'), hospital=None)
        owner = User.objects.create_superuser(email='owner@t.com', password='pw')
        c = Client(); c.force_login(owner)
        resp = c.get(reverse('lab:test_catalog'))
        self.assertContains(resp, 'Local CBC')
        self.assertNotContains(resp, 'Beta CBC')


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

    def test_hospital_less_user_sees_no_patient_index(self):
        """The offline patient-index JSON reuses the list's scoping, so a
        hospital-less non-superuser must get an empty registry, not everyone's."""
        resp = self.client.get(reverse('patient_index'))
        self.assertEqual(resp.status_code, 200)
        names = [p['name'] for p in resp.json()['patients']]
        self.assertNotIn('Alpha Patient', names)
        self.assertNotIn('Beta Patient', names)

    def test_hospital_less_user_cannot_open_a_tenant_record(self):
        resp = self.client.get(reverse('prescription_detail', args=[self.rx2.pk]))
        self.assertIn(resp.status_code, (403, 404))

    # These three lists sit on models with no hospital column (TestOrder,
    # ImagingStudy, Appointment) and were left on the fail-OPEN
    # `if request.user.hospital:` guard, so a hospital-less user read every
    # tenant's clinical records. Regression guard for that leak.
    def test_hospital_less_user_sees_no_appointments(self):
        resp = self.client.get(reverse('appointment_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beta Patient')

    def test_hospital_less_user_sees_no_lab_orders(self):
        from lab.models import TestOrder
        TestOrder.objects.create(patient=self.patient2)
        resp = self.client.get(reverse('lab:order_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beta Patient')

    def test_hospital_less_user_sees_no_imaging_studies(self):
        from imaging.models import ImagingStudy
        ImagingStudy.objects.create(patient=self.patient2, study_name='Beta Scan',
                                    modality='XRAY', price=Decimal('0'))
        resp = self.client.get(reverse('imaging:study_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beta Patient')

    # Doctor has no hospital column (scoped via user__hospital); doctor_list was on
    # the fail-OPEN `if request.user.hospital:` guard, leaking every tenant's doctors.
    def test_hospital_less_user_sees_no_doctors(self):
        resp = self.client.get(reverse('doctor_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Dr Beta')

    # reports.visual_analytics aggregated InvoiceItem + Appointment (no TenantManager)
    # behind a fail-OPEN `if hospital:`, so a hospital-less user saw every tenant's
    # doctor workload. A DONE appointment this month must not leak into the analytics.
    def test_hospital_less_user_sees_no_analytics_workload(self):
        from django.utils import timezone
        Appointment.objects.create(patient=self.patient2, doctor=self.doctor2,
                                   status='DONE', appointment_date=timezone.localdate())
        resp = self.client.get(reverse('visual_analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Dr Beta')


class AuthenticationRequiredTest(TwoTenantSetup):
    """Anonymous users get bounced to login — never served data."""

    # NB: the bare root '/' ('dashboard') now serves the public marketing landing
    # to anonymous visitors (see seo_views.home), so it is deliberately NOT here;
    # '/dashboard/' ('dashboard_page') remains the login-walled app dashboard.
    PROTECTED = ['dashboard_page', 'patient_list', 'medicine_list', 'sale_create',
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

    def test_only_an_admin_reaches_the_admin_dashboard(self):
        """`/manage/dashboard/admin/` was `@login_required` and nothing more.

        It renders `user_mgmt.overview.build`, so ANY signed-in user — a nurse, a
        wholesale operator — got the owner's view of the hospital: the day's
        revenue and what is still unpaid, the attention list, the OPD board, and
        the recent **audit feed** (who signed in and when). The audit trail is
        tenant-scoped precisely because it is the most sensitive page in the
        product; this route handed it to every role inside the tenant.
        """
        for role in ('NURSE', 'PHARMACIST', 'WHOLESALE', 'LABTECH',
                     'RECEPTIONIST', 'ACCOUNTANT'):
            with self.subTest(role=role):
                u = User.objects.create_user(email=f'dash.{role}@t.com',
                                             password='pw', role=role,
                                             hospital=self.h1)
                c = Client(); c.force_login(u)
                self.assertEqual(
                    c.get(reverse('user_mgmt:admin_dashboard')).status_code, 403,
                    f'{role} can open the admin overview')
        c = Client(); c.force_login(self.admin1)
        self.assertEqual(
            c.get(reverse('user_mgmt:admin_dashboard')).status_code, 200)

    def test_the_legacy_role_dashboards_do_not_render_someone_elses(self):
        """`/manage/dashboard/pharmacist|lab|sonographer|manager/` rendered a fixed
        role's dashboard to whoever asked. They are legacy URL names kept alive for
        old links, so they now just route the caller to their own."""
        nurse = User.objects.create_user(email='legacy.nurse@t.com', password='pw',
                                         role='NURSE', hospital=self.h1)
        c = Client(); c.force_login(nurse)
        for name in ('pharmacist_dashboard', 'lab_dashboard',
                     'sonographer_dashboard', 'manager_dashboard'):
            with self.subTest(view=name):
                resp = c.get(reverse(f'user_mgmt:{name}'))
                self.assertEqual(resp.status_code, 302,
                                 f'{name} still renders a dashboard directly')

    def test_a_signed_in_user_is_not_shown_the_sign_in_form(self):
        """To somebody already signed in, a login form reads as "you have been
        logged out" — and on a phone /login/ is one mis-tap away."""
        c = Client(); c.force_login(self.admin1)
        resp = c.get('/login/')
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('login', resp['Location'])


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
