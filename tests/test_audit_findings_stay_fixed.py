"""One test per finding from the A-to-Z audit, written as the attack.

The point of this file is that it can be re-run and believed. Each test does
the thing that used to work and asserts it no longer does — not "is the guard
present in the source", which passes for a guard that is present and wrong.

The per-finding detail lives in `tests/test_object_scoping.py`,
`patients/tests_portal.py` and `opd/tests_reception.py`; this is the roll-call.

    python manage.py test tests.test_audit_findings_stay_fixed --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from opd.models import Appointment, Department, Doctor
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=365)


class Fixture(TestCase):
    def setUp(self):
        # A user has to exist or `FirstRunMiddleware` sends every request to the
        # setup wizard, and every assertion below passes for the wrong reason.
        self.staff = User.objects.create_user(email='staff@shc.test', password='pw')
        self.mine = Hospital.objects.create(name='Shaheen Health Care', slug='shc',
                                            expiry_date=_future())
        self.theirs = Hospital.objects.create(name='Other Clinic', slug='other',
                                              expiry_date=_future())
        set_current_hospital(self.theirs)
        try:
            dept = Department.objects.create(name='Med', hospital=self.theirs)
            self.their_doctor = Doctor.objects.create(
                full_name='Their Doctor', department=dept, hospital=self.theirs,
                opd_fee=Decimal('500'))
            self.their_patient = Patient.objects.create(
                full_name='Ayesha Bibi', gender='F', hospital=self.theirs,
                phone='03211234567', mrn='OTH-000001')
            self.their_appt = Appointment.objects.create(
                patient=self.their_patient, doctor=self.their_doctor,
                appointment_date=date.today())
        finally:
            clear_current_hospital()


# --- 1. the public patient search --------------------------------------------

class Finding01_PortalSearchIsOneHospitalOnly(Fixture):

    def test_the_bare_domain_cannot_search_a_tenants_register(self):
        """No hospital chosen: it used to search every one of them."""
        r = self.client.get(reverse('patient_portal_lookup'), {'query': '03211234567'})
        body = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(str(self.their_patient.portal_token), body)
        self.assertNotIn('Ayesha Bibi', body)

    def test_one_hospital_cannot_be_used_to_search_another(self):
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': '03211234567', 'hospital': self.mine.slug})
        self.assertNotIn(str(self.their_patient.portal_token), r.content.decode())

    def test_a_name_alone_opens_nothing(self):
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': 'Ayesha', 'hospital': self.theirs.slug})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(str(self.their_patient.portal_token), r.content.decode())

    def test_the_row_id_opens_nothing(self):
        """`Q(id=num)` sat alongside the MRN match, so the primary key — a
        number the patient never sees — opened the record.

        The MRN is moved well away from the pk first, or this accidentally
        re-tests the (legitimate) MRN lookup and passes for the wrong reason.
        """
        self.their_patient.mrn = 'OTH-004242'
        self.their_patient.save(update_fields=['mrn'])
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': str(self.their_patient.pk).zfill(7),
                             'hospital': self.theirs.slug})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(str(self.their_patient.portal_token), r.content.decode())

    def test_the_real_patient_can_still_get_in_with_their_own_number(self):
        """The feature has to survive the fix."""
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': '03211234567', 'hospital': self.theirs.slug})
        self.assertRedirects(
            r, reverse('patient_portal_hub', args=[self.their_patient.portal_token]))


@override_settings(PORTAL_LOOKUP_MAX=3, PORTAL_LOOKUP_WINDOW_SECONDS=60)
class Finding01b_PortalSearchIsRateLimited(Fixture):
    """MRNs run in sequence, so the scoping above still leaves them walkable
    one hospital at a time. The limit has to be counted somewhere every worker
    process can see — the first attempt used `cache`, and no CACHES is
    configured, so that was per-process LocMemCache."""

    def _search(self, n):
        url = reverse('patient_portal_lookup')
        return [self.client.get(url, {'query': f'OTH-{i:06d}',
                                      'hospital': self.theirs.slug})
                for i in range(1, n + 1)]

    def test_it_stops_after_the_limit(self):
        last = self._search(5)[-1]
        self.assertIn('Too many searches', last.content.decode())

    def test_it_is_not_stored_in_a_per_process_cache(self):
        from django.core.cache import cache

        cache.clear()          # would reset a cache-based counter entirely
        self._search(4)
        cache.clear()
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': 'OTH-000009', 'hospital': self.theirs.slug})
        self.assertIn('Too many searches', r.content.decode(),
                      'clearing the cache must not hand back a fresh quota — '
                      'on Passenger that is what a new worker process does')

    def test_a_patient_who_types_it_wrong_twice_is_not_locked_out(self):
        for _ in range(2):
            self.client.get(reverse('patient_portal_lookup'),
                            {'query': 'OTH-999999', 'hospital': self.theirs.slug})
        r = self.client.get(reverse('patient_portal_lookup'),
                            {'query': '03211234567', 'hospital': self.theirs.slug})
        self.assertEqual(r.status_code, 302)


# --- 2. the public queue tracker ---------------------------------------------

class Finding02_TrackerIsNotWalkable(Fixture):

    def test_counting_appointment_ids_returns_nothing(self):
        for pk in range(1, 6):
            self.assertEqual(self.client.get(f'/opd/track/{pk}/').status_code, 404)

    def test_the_token_still_works_for_the_patient(self):
        r = self.client.get(reverse('patient_token_track',
                                    args=[self.their_appt.track_token]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('Ayesha Bibi', r.content.decode())

    def test_two_appointments_do_not_share_a_token(self):
        second = Appointment.objects.create(
            patient=self.their_patient, doctor=self.their_doctor,
            appointment_date=date.today() + timedelta(days=1))
        self.assertNotEqual(second.track_token, self.their_appt.track_token)


# --- 3. printed QR codes ------------------------------------------------------

class Finding03_QrCodesDoNotLeaveTheServer(TestCase):

    def test_no_template_calls_an_external_qr_service(self):
        from pathlib import Path

        from django.conf import settings
        offenders = [p.name for p in Path(settings.BASE_DIR).glob('templates/**/*.html')
                     if 'qrserver' in p.read_text(encoding='utf-8')]
        self.assertEqual(offenders, [], ', '.join(offenders))

    def test_the_slip_renders_its_qr_inline(self):
        try:
            import qrcode  # noqa: F401
        except Exception:
            self.skipTest('qrcode is an optional dependency and is not installed')
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get('/')
        out = Template(
            "{% load qr %}{% url 'patient_portal_lookup' as p %}"
            "{% qr_url_data_uri p %}"
        ).render(Context({'request': request}))
        self.assertTrue(out.startswith('data:image/png;base64,'))


# --- 4-6. the three doctor screens -------------------------------------------

class Finding0406_AnotherHospitalsDoctorCannotBeTouched(Fixture):

    def setUp(self):
        super().setUp()
        self.admin = self.staff
        self.admin.role, self.admin.hospital = 'ADMIN', self.mine
        self.admin.save()
        self.client.force_login(self.admin)

    def test_edit(self):
        self.assertEqual(
            self.client.get(reverse('doctor_edit', args=[self.their_doctor.pk])).status_code,
            404)

    def test_delete(self):
        self.client.post(reverse('doctor_delete', args=[self.their_doctor.pk]),
                         {'action': 'archive'})
        self.their_doctor.refresh_from_db()
        self.assertTrue(self.their_doctor.is_active)

    def test_off_duty(self):
        self.assertEqual(
            self.client.post(reverse('doctor_availability_toggle',
                                     args=[self.their_doctor.pk]),
                             {'available': '0'}).status_code, 404)

    def test_my_own_doctor_is_still_editable(self):
        set_current_hospital(self.mine)
        try:
            dept = Department.objects.create(name='Mine', hospital=self.mine)
            mine = Doctor.objects.create(full_name='My Doctor', department=dept,
                                         hospital=self.mine)
        finally:
            clear_current_hospital()
        self.assertEqual(
            self.client.get(reverse('doctor_edit', args=[mine.pk])).status_code, 200)


# --- 7. Rx presets -------------------------------------------------------------

class Finding07_PresetsDoNotLandInSomebodyElsesHospital(TestCase):

    def test_a_hospital_less_admin_writes_nowhere_near_a_tenant(self):
        from prescriptions.models import RxPreset
        root = User.objects.create_user(email='root@x.test', password='pw',
                                        is_superuser=True, is_staff=True)
        victim = Hospital.objects.create(name='First Customer', slug='first',
                                         expiry_date=_future())
        root.role = 'ADMIN'
        root.save()
        self.client.force_login(root)
        self.client.post(reverse('preset_create'), {
            'name': 'Flu pack', 'complaint': '', 'diagnosis': '', 'notes': '',
            'items-TOTAL_FORMS': '0', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
        })
        self.assertFalse(RxPreset.all_objects.filter(hospital=victim).exists())


# --- 8. the appointment status endpoint ---------------------------------------

class Finding08_StatusIsAPostAndIsScoped(Fixture):

    def setUp(self):
        super().setUp()
        self.admin = self.staff
        self.admin.role, self.admin.hospital = 'ADMIN', self.mine
        self.admin.save()
        self.client.force_login(self.admin)

    def test_a_get_cannot_write(self):
        r = self.client.get(reverse('appointment_update_status',
                                    args=[self.their_appt.pk]), {'status': 'DONE'})
        self.assertEqual(r.status_code, 405)

    def test_another_hospitals_appointment_cannot_be_moved(self):
        self.client.post(reverse('appointment_update_status',
                                 args=[self.their_appt.pk]), {'status': 'DONE'})
        self.their_appt.refresh_from_db()
        self.assertNotEqual(self.their_appt.status, 'DONE')

    def test_the_route_exists_at_all(self):
        """It never did — the dropdown had always answered 404."""
        self.assertEqual(reverse('appointment_update_status', args=[1]),
                         '/opd/appointments/1/status/')


# --- 9. billing search, and HSTS ----------------------------------------------

class Finding09_BillingUsesTheSharedSearch(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(email='a@h.test', password='pw')
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        self.admin.role, self.admin.hospital = 'ADMIN', self.h
        self.admin.save()
        self.client.force_login(self.admin)
        set_current_hospital(self.h)
        try:
            self.patient = Patient.objects.create(
                full_name='Ali Raza Khan', gender='M', hospital=self.h,
                phone='0311-1111111')
        finally:
            clear_current_hospital()

    def test_a_phone_typed_without_dashes_finds_them(self):
        r = self.client.get(reverse('patient_billing_list'), {'q': '03111111111'})
        self.assertIn('Ali Raza Khan', r.content.decode())

    def test_a_first_name_and_a_surname_find_them(self):
        r = self.client.get(reverse('patient_billing_list'), {'q': 'ali khan'})
        self.assertIn('Ali Raza Khan', r.content.decode())


class Finding09b_HstsIsSet(TestCase):

    def test_it_is_configured_when_ssl_is_on(self):
        """Only reached when USE_SSL is on, so the plain-http LAN build is
        unaffected. Read from the module rather than `settings`, because the
        test settings deliberately run without SSL."""
        import re
        from pathlib import Path

        from django.conf import settings
        src = (Path(settings.BASE_DIR) / 'pharma_mgmt' / 'settings.py').read_text(
            encoding='utf-8')
        self.assertRegex(src, r'SECURE_HSTS_SECONDS\s*=')
        block = src[src.index('if USE_SSL:'):]
        self.assertIn('SECURE_HSTS_SECONDS', block.split('\nelse:')[0],
                      'HSTS must sit inside the USE_SSL branch — the LAN build '
                      'serves plain http and would lock itself out')
