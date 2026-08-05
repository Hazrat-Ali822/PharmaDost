"""A pending prescription whose doctor has no linked user account must not 500 the
pages that list it (sale list, POS, pharmacist dashboard). Regression for a
`doctor.user.email` lookup on a None user."""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor, Appointment
from prescriptions.models import Prescription
from saas.utils import set_current_hospital, clear_current_hospital


class PendingRxDoctorWithoutUserTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        set_current_hospital(self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M',
                                              age_years=30, hospital=self.h)
        # a doctor with NO linked user account (perfectly valid — many are paper-only)
        self.doctor = Doctor.objects.create(full_name='Dr. Sara Ahmed', opd_fee=500)
        appt = Appointment.objects.create(patient=self.patient, doctor=self.doctor,
                                          appointment_date=timezone.localdate())
        Prescription.objects.create(appointment=appt, status='PENDING')
        self.c = Client(); self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def test_pages_that_list_the_pending_rx_render(self):
        for url in ['/sales/list/', '/sales/new/', '/manage/dashboard/pharmacist/']:
            resp = self.c.get(url)
            self.assertIn(resp.status_code, (200, 302), f'{url} -> {resp.status_code}')
