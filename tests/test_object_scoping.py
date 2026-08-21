"""The screens a stranger can reach, and the pk-taking views inside the app.

Two audits in one file, because they are the same mistake at different depths:
a row fetched by an id out of the URL, with nothing checking who is allowed to
have it.

**The anonymous surface.** `/portal/` and `/opd/track/` are deliberately
login-free — a patient scans a QR on their slip. Neither scoped. `Appointment`
has no `hospital` column and no manager at all, and `Patient.objects` is a
`TenantManager` that hands back *everything* to an anonymous request, because
nothing binds a tenant and the thread is not strict. So on the bare platform
domain, `/portal/` was a search box over every customer's patient register and
`/opd/track/1/`, `/2/`, `/3/` walked every appointment on the platform.

**Inside the app.** `Doctor` has a `hospital` column but no manager, so every
view taking a doctor pk must narrow it itself. `payout_doctor` was fixed for
exactly this; `doctor_edit`, `doctor_delete` and `doctor_availability_toggle`
were left as bare `get_object_or_404(Doctor, pk=pk)`.

    python manage.py test tests.test_object_scoping --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from opd.models import Appointment, Department, Doctor
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=365)


class TwoHospitals(TestCase):
    """One tenant's admin signed in, and another tenant's rows to reach for."""

    def setUp(self):
        self.mine = Hospital.objects.create(name='Shaheen Health Care', slug='shc',
                                            expiry_date=_future())
        self.theirs = Hospital.objects.create(name='Other Clinic', slug='other',
                                              expiry_date=_future())
        self.admin = User.objects.create_user(email='a@shc.test', password='pw')
        self.admin.role, self.admin.hospital = 'ADMIN', self.mine
        self.admin.save()
        self.client.force_login(self.admin)

        set_current_hospital(self.theirs)
        try:
            dept = Department.objects.create(name='Their Medicine', hospital=self.theirs)
            self.their_doctor = Doctor.objects.create(
                full_name='Their Doctor', department=dept, hospital=self.theirs,
                opd_fee=Decimal('500'))
            self.their_patient = Patient.objects.create(
                full_name='Their Patient', gender='M', hospital=self.theirs,
                phone='03009998888')
            self.their_appt = Appointment.objects.create(
                patient=self.their_patient, doctor=self.their_doctor,
                appointment_date=date.today())
        finally:
            clear_current_hospital()


class AnotherHospitalsDoctorIsOutOfReachTest(TwoHospitals):
    """The three screens that CHANGE a doctor all took a bare pk."""

    def test_edit_page(self):
        r = self.client.get(reverse('doctor_edit', args=[self.their_doctor.pk]))
        self.assertEqual(r.status_code, 404)

    def test_edit_cannot_be_posted_either(self):
        self.client.post(reverse('doctor_edit', args=[self.their_doctor.pk]), {
            'full_name': 'Renamed By Another Hospital', 'opd_fee': '1',
            'followup_fee': '0', 'followup_valid_days': '0', 'share_percent': '0',
            'schedules-TOTAL_FORMS': '0', 'schedules-INITIAL_FORMS': '0',
            'schedules-MIN_NUM_FORMS': '0', 'schedules-MAX_NUM_FORMS': '1000',
        })
        self.their_doctor.refresh_from_db()
        self.assertEqual(self.their_doctor.full_name, 'Their Doctor')
        self.assertEqual(self.their_doctor.opd_fee, Decimal('500'))

    def test_delete(self):
        r = self.client.post(reverse('doctor_delete', args=[self.their_doctor.pk]),
                             {'action': 'archive'})
        self.assertEqual(r.status_code, 404)
        self.their_doctor.refresh_from_db()
        self.assertTrue(self.their_doctor.is_active)

    def test_marking_them_off_duty_for_the_day(self):
        r = self.client.post(reverse('doctor_availability_toggle',
                                     args=[self.their_doctor.pk]),
                             {'available': '0'})
        self.assertEqual(r.status_code, 404)


class AnotherHospitalsAppointmentIsOutOfReachTest(TwoHospitals):

    def _my_appointment(self):
        patient = Patient.objects.create(full_name='Mine', gender='M',
                                         hospital=self.mine)
        set_current_hospital(self.mine)
        try:
            dept = Department.objects.create(name='Mine', hospital=self.mine)
            doc = Doctor.objects.create(full_name='Mine Doc', department=dept,
                                        hospital=self.mine)
            return Appointment.objects.create(patient=patient, doctor=doc,
                                              appointment_date=date.today())
        finally:
            clear_current_hospital()

    def test_the_status_cannot_be_changed(self):
        r = self.client.post(reverse('appointment_update_status',
                                     args=[self.their_appt.pk]),
                             {'status': 'DONE'})
        self.assertEqual(r.status_code, 404)
        self.their_appt.refresh_from_db()
        self.assertNotEqual(self.their_appt.status, 'DONE')

    def test_the_status_endpoint_refuses_a_GET(self):
        """It wrote on GET, so an `<img src=".../status/?status=DONE">` on any
        page fired it from a signed-in staff member's browser, with no CSRF
        token involved at all."""
        appt = self._my_appointment()
        r = self.client.get(reverse('appointment_update_status', args=[appt.pk]),
                            {'status': 'DONE'})
        self.assertEqual(r.status_code, 405)
        appt.refresh_from_db()
        self.assertNotEqual(appt.status, 'DONE')

    def test_my_own_appointment_still_moves(self):
        appt = self._my_appointment()
        r = self.client.post(reverse('appointment_update_status', args=[appt.pk]),
                             {'status': 'DONE'})
        self.assertEqual(r.status_code, 200)
        appt.refresh_from_db()
        self.assertEqual(appt.status, 'DONE')


class RxPresetsGoToTheRightHospitalTest(TestCase):
    """`preset.hospital = request.user.hospital or Hospital.objects.first()`
    filed a hospital-less user's preset into whichever tenant had the lowest
    id — a real customer's data. And on the desktop build, where there are no
    Hospital rows at all, `.first()` was None against a NOT NULL column, so the
    feature crashed outright."""

    def setUp(self):
        self.admin = User.objects.create_user(email='root@x.test', password='pw',
                                              is_superuser=True, is_staff=True)
        self.admin.role = 'ADMIN'
        self.admin.save()
        self.client.force_login(self.admin)

    def _post(self):
        return self.client.post(reverse('preset_create'), {
            'name': 'Flu pack', 'complaint': '', 'diagnosis': '', 'notes': '',
            'items-TOTAL_FORMS': '0', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
        })

    def test_a_hospital_less_admin_does_not_write_into_a_tenant(self):
        from prescriptions.models import RxPreset
        victim = Hospital.objects.create(name='First Customer', slug='first',
                                         expiry_date=_future())
        self._post()
        self.assertFalse(RxPreset.all_objects.filter(hospital=victim).exists(),
                         'a hospital-less user must never write into a tenant')

    def test_a_single_site_install_can_create_one_at_all(self):
        from prescriptions.models import RxPreset
        self._post()
        self.assertTrue(RxPreset.all_objects.filter(name='Flu pack').exists())


class PrintedQrCodesStayInTheBuildingTest(TestCase):
    """Four printed documents — OPD slip, patient bill, lab report, imaging
    report — built an `<img src>` pointing at api.qrserver.com with the target
    URL in the query string. Those URLs carry the patient's `portal_token`, the
    one secret protecting their prescriptions, lab results and bills, so every
    print handed it to a third party's access log. It also needed the internet,
    so the clinic LAN build printed a broken image."""

    def test_no_template_sends_a_url_to_an_external_qr_service(self):
        from pathlib import Path

        from django.conf import settings
        offenders = [p.name for p in Path(settings.BASE_DIR).glob('templates/**/*.html')
                     if 'qrserver' in p.read_text(encoding='utf-8')]
        self.assertEqual(offenders, [],
                         'these templates send the patient portal token to an '
                         'external QR service: ' + ', '.join(offenders))

    def test_the_local_tag_renders_a_real_png(self):
        from billing.templatetags.qr import qr_data_uri
        out = qr_data_uri('https://example.test/portal/abc/')
        # `qrcode` is an optional dependency; when it is absent the tag returns
        # '' so a bill still prints. Only assert the shape when it is there.
        try:
            import qrcode  # noqa: F401
        except Exception:
            self.assertEqual(out, '')
        else:
            self.assertTrue(out.startswith('data:image/png;base64,'))
