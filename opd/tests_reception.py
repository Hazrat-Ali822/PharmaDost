"""The front desk: find or register a patient, pick a department, book a sitting
doctor, print the token slip."""
from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from billing.models import Invoice
from opd.models import (Appointment, Department, Doctor, DoctorAvailabilityOverride,
                        DoctorSchedule)
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _all_day(doctor, on=None):
    """Timings that cover right now, whatever day the suite runs on."""
    today = (on or timezone.localdate())
    return DoctorSchedule.objects.create(
        doctor=doctor, weekday=today.weekday(),
        start_time=time(0, 1), end_time=time(23, 58))


class ReceptionBase(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(email='r@r.com', password='pw',
                                             role='RECEPTIONIST', hospital=self.h)
        self.medicine = Department.objects.create(name='Medicine', hospital=self.h)
        self.gynae = Department.objects.create(name='Gynaecology', hospital=self.h)

        docuser = User.objects.create_user(email='d1@d.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        self.sitting = Doctor.objects.create(user=docuser, full_name='Ahmed Ali',
                                             department=self.medicine,
                                             opd_fee=Decimal('500'),
                                             followup_fee=Decimal('200'))
        _all_day(self.sitting)

        self.away = Doctor.objects.create(full_name='Bilal Khan',
                                          department=self.gynae,
                                          opd_fee=Decimal('800'))
        # no schedule at all -> never shows as sitting

        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()


class AvailabilityTest(ReceptionBase):
    def test_a_doctor_inside_their_timings_is_sitting(self):
        self.assertTrue(self.sitting.is_available_now)

    def test_a_doctor_with_no_timings_is_never_sitting(self):
        state = self.away.availability()
        self.assertFalse(state['available'])
        self.assertEqual(state['label'], 'Not in OPD today')

    def test_timings_later_today_say_when_they_arrive(self):
        doctor = Doctor.objects.create(full_name='Late Arrival', department=self.medicine)
        now = timezone.localtime()
        if now.hour >= 22:
            self.skipTest('no room left in the day to schedule a later sitting')
        DoctorSchedule.objects.create(doctor=doctor, weekday=now.date().weekday(),
                                      start_time=time(now.hour + 1, 0),
                                      end_time=time(23, 59))
        state = doctor.availability()
        self.assertFalse(state['available'])
        self.assertTrue(state['label'].startswith('From '), state['label'])

    def test_marking_off_today_beats_the_timings(self):
        DoctorAvailabilityOverride.objects.create(
            doctor=self.sitting, date=timezone.localdate(),
            available=False, note='On leave — back Monday')
        state = self.sitting.availability()
        self.assertFalse(state['available'])
        self.assertEqual(state['label'], 'On leave — back Monday')

    def test_marking_available_beats_having_no_timings(self):
        DoctorAvailabilityOverride.objects.create(
            doctor=self.away, date=timezone.localdate(), available=True)
        self.assertTrue(self.away.is_available_now)

    def test_yesterdays_leave_does_not_hide_a_doctor_today(self):
        DoctorAvailabilityOverride.objects.create(
            doctor=self.sitting, date=timezone.localdate() - timedelta(days=1),
            available=False)
        self.assertTrue(self.sitting.is_available_now)

    def test_the_board_separates_sitting_from_away(self):
        resp = self.client.get(reverse('doctor_availability_board'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ahmed Ali')
        self.assertContains(resp, 'Bilal Khan')
        self.assertContains(resp, 'Sitting now (1)')

    def test_one_click_marks_a_doctor_off_for_today_only(self):
        resp = self.client.post(
            reverse('doctor_availability_toggle', args=[self.sitting.pk]),
            {'available': '0', 'note': 'Emergency at home'})
        self.assertEqual(resp.status_code, 302)
        self.sitting.refresh_from_db()
        self.assertFalse(self.sitting.is_available_now)
        override = DoctorAvailabilityOverride.objects.get(doctor=self.sitting)
        self.assertEqual(override.date, timezone.localdate())
        self.assertEqual(override.set_by, self.user)

    def test_marking_back_on_drops_the_override(self):
        self.client.post(reverse('doctor_availability_toggle', args=[self.sitting.pk]),
                         {'available': '0'})
        self.client.post(reverse('doctor_availability_toggle', args=[self.sitting.pk]),
                         {'available': '1'})
        self.assertFalse(DoctorAvailabilityOverride.objects
                         .filter(doctor=self.sitting, date=timezone.localdate()).exists())
        self.sitting.refresh_from_db()
        self.assertTrue(self.sitting.is_available_now)


class ReceptionDeskTest(ReceptionBase):
    def test_the_desk_offers_new_and_old(self):
        resp = self.client.get(reverse('reception_desk'))
        self.assertContains(resp, 'New Patient')
        self.assertContains(resp, 'Old Patient')

    def test_an_old_patient_is_found_by_mrn(self):
        p = Patient.objects.create(full_name='Sara Bibi', phone='03001234567',
                                   hospital=self.h)
        resp = self.client.get(reverse('reception_desk'), {'q': p.mrn})
        self.assertContains(resp, 'Sara Bibi')
        self.assertContains(resp, 'Book Visit')

    def test_an_old_patient_is_found_by_mobile_number(self):
        Patient.objects.create(full_name='Sara Bibi', phone='03001234567', hospital=self.h)
        resp = self.client.get(reverse('reception_desk'), {'q': '03001234567'})
        self.assertContains(resp, 'Sara Bibi')

    def test_a_cnic_typed_without_dashes_still_matches(self):
        Patient.objects.create(full_name='Imran Shah', cnic='35202-1234567-1',
                               hospital=self.h)
        resp = self.client.get(reverse('reception_desk'), {'q': '3520212345671'})
        self.assertContains(resp, 'Imran Shah')

    def test_another_tenants_patient_is_never_found(self):
        other = Hospital.objects.create(name='Other', slug='other',
                                        expiry_date=date.today() + timedelta(days=30))
        Patient.objects.create(full_name='Rival Patient', phone='03001234567',
                               hospital=other)
        resp = self.client.get(reverse('reception_desk'), {'q': '03001234567'})
        self.assertNotContains(resp, 'Rival Patient')

    def test_no_match_offers_registration(self):
        resp = self.client.get(reverse('reception_desk'), {'q': '03009999999'})
        self.assertContains(resp, 'Register as a new patient')


class VisitBookingTest(ReceptionBase):
    def test_the_form_hides_doctors_who_are_not_sitting(self):
        resp = self.client.get(reverse('visit_create'))
        self.assertEqual(resp.status_code, 200)
        # both are rendered, but the away one starts hidden behind the toggle
        self.assertContains(resp, 'Ahmed Ali')
        self.assertContains(resp, 'data-away="1"')
        self.assertContains(resp, 'Also show doctors who are not sitting')

    def test_registering_and_booking_happen_in_one_submit(self):
        resp = self.client.post(reverse('visit_create'), {
            'full_name': 'Walk In', 'gender': 'M', 'age_years': '40',
            'phone': '03001112222', 'mrn': '',
            'department': self.medicine.pk, 'doctor': self.sitting.pk,
            'visit_type': 'OPD',
            'appointment_date': timezone.localdate().isoformat(),
            'slot_time': '10:30',
        })
        self.assertEqual(resp.status_code, 302)
        patient = Patient.objects.get(full_name='Walk In')
        self.assertEqual(patient.mrn, 'SGH-000001')
        appointment = Appointment.objects.get(patient=patient)
        self.assertEqual(appointment.doctor, self.sitting)
        self.assertEqual(appointment.token_no, 1)
        # the slip is where the desk lands
        self.assertEqual(resp['Location'], reverse('appointment_slip', args=[appointment.pk]))

    def test_booking_raises_the_consultation_invoice(self):
        self.client.post(reverse('visit_create'), {
            'full_name': 'Billed', 'gender': 'F', 'mrn': '',
            'doctor': self.sitting.pk, 'visit_type': 'OPD',
            'appointment_date': timezone.localdate().isoformat(),
        })
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, Decimal('500'))

    def test_a_follow_up_is_billed_at_the_follow_up_fee(self):
        self.client.post(reverse('visit_create'), {
            'full_name': 'Return Visit', 'gender': 'F', 'mrn': '',
            'doctor': self.sitting.pk, 'visit_type': 'FOLLOWUP',
            'appointment_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(Invoice.objects.get().total, Decimal('200'))

    def test_an_existing_patient_is_booked_without_re_entering_anything(self):
        patient = Patient.objects.create(full_name='Known Patient', gender='M',
                                         hospital=self.h)
        resp = self.client.get(reverse('visit_create'), {'patient': patient.pk})
        self.assertContains(resp, 'Known Patient')
        self.assertContains(resp, patient.mrn)

        resp = self.client.post(reverse('visit_create'), {
            'patient_id': patient.pk,
            'doctor': self.sitting.pk, 'visit_type': 'OPD',
            'appointment_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Patient.objects.filter(full_name='Known Patient').count(), 1)
        self.assertTrue(Appointment.objects.filter(patient=patient).exists())

    def test_a_doctor_from_another_department_is_rejected(self):
        resp = self.client.post(reverse('visit_create'), {
            'full_name': 'Wrong Dept', 'gender': 'M', 'mrn': '',
            'department': self.medicine.pk, 'doctor': self.away.pk,
            'visit_type': 'OPD',
            'appointment_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'is not in Medicine')
        self.assertFalse(Appointment.objects.exists())

    def test_an_off_duty_doctor_can_still_be_booked_for_an_emergency(self):
        resp = self.client.post(reverse('visit_create'), {
            'full_name': 'Emergency', 'gender': 'M', 'mrn': '',
            'department': self.gynae.pk, 'doctor': self.away.pk,
            'visit_type': 'EMERGENCY',
            'appointment_date': timezone.localdate().isoformat(),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Appointment.objects.filter(doctor=self.away).exists())

    def test_tokens_count_up_per_doctor_per_day(self):
        for name in ('First', 'Second', 'Third'):
            self.client.post(reverse('visit_create'), {
                'full_name': name, 'gender': 'M', 'mrn': '',
                'doctor': self.sitting.pk, 'visit_type': 'OPD',
                'appointment_date': timezone.localdate().isoformat(),
            })
        tokens = list(Appointment.objects.order_by('token_no')
                      .values_list('token_no', flat=True))
        self.assertEqual(tokens, [1, 2, 3])


class SlipTest(ReceptionBase):
    def setUp(self):
        super().setUp()
        self.patient = Patient.objects.create(full_name='Slip Patient', gender='M',
                                              phone='03001234567',
                                              allergies='Penicillin', hospital=self.h)
        self.appointment = Appointment.objects.create(
            patient=self.patient, doctor=self.sitting,
            appointment_date=timezone.localdate(), visit_type='OPD')

    def test_the_slip_carries_what_the_patient_needs(self):
        resp = self.client.get(reverse('appointment_slip', args=[self.appointment.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.patient.mrn)
        self.assertContains(resp, 'Slip Patient')
        self.assertContains(resp, 'Ahmed Ali')
        self.assertContains(resp, 'Medicine')                 # department
        self.assertContains(resp, 'Token')
        self.assertContains(resp, 'Rs 500')
        self.assertContains(resp, 'Penicillin')               # allergies travel with them
        self.assertContains(resp, 'window.print()')

    def test_another_tenants_slip_is_not_reachable(self):
        other = Hospital.objects.create(name='Other', slug='other',
                                        expiry_date=date.today() + timedelta(days=30))
        their_patient = Patient.objects.create(full_name='Theirs', mrn='OTH-1',
                                               hospital=other)
        their_appointment = Appointment.objects.create(
            patient=their_patient, doctor=self.sitting,
            appointment_date=timezone.localdate())
        resp = self.client.get(reverse('appointment_slip', args=[their_appointment.pk]))
        self.assertEqual(resp.status_code, 404)


class DepartmentTest(ReceptionBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.client.force_login(self.admin)

    def test_departments_list_their_doctors(self):
        resp = self.client.get(reverse('department_list'))
        self.assertContains(resp, 'Medicine')
        self.assertContains(resp, 'Ahmed Ali')

    def test_a_department_with_doctors_is_hidden_rather_than_deleted(self):
        resp = self.client.post(reverse('department_delete', args=[self.medicine.pk]))
        self.assertEqual(resp.status_code, 302)
        self.medicine.refresh_from_db()
        self.assertFalse(self.medicine.is_active)
        self.sitting.refresh_from_db()
        self.assertEqual(self.sitting.department, self.medicine)   # not unfiled

    def test_an_empty_department_is_deleted_outright(self):
        empty = Department.objects.create(name='ENT', hospital=self.h)
        self.client.post(reverse('department_delete', args=[empty.pk]))
        self.assertFalse(Department.objects.filter(pk=empty.pk).exists())
