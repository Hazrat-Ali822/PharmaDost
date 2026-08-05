"""The manual 'Create Invoice' screen must not 500 when no appointment is chosen,
and must bill correctly when one is. Regression for a NoneType.doctor crash."""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor, Appointment
from saas.utils import set_current_hospital, clear_current_hospital


class InvoiceCreateTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Alpha', slug='alpha',
                                         expiry_date=date.today() + timedelta(days=365))
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        set_current_hospital(self.h)
        self.patient = Patient.objects.create(full_name='Ali', gender='M',
                                              age_years=30, hospital=self.h)
        self.doctor = Doctor.objects.create(full_name='Dr Sara', opd_fee=500)
        self.appt = Appointment.objects.create(
            patient=self.patient, doctor=self.doctor,
            appointment_date=timezone.localdate(), visit_type='OPD')
        self.c = Client(); self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def test_submitting_without_an_appointment_does_not_500(self):
        from billing.models import Invoice
        resp = self.c.post('/billing/create/', {
            'patient': self.patient.pk, 'payment_method': 'CASH',
            'discount': '0', 'paid': '0'})
        self.assertEqual(resp.status_code, 200)          # re-renders with an error, not a crash
        self.assertContains(resp, 'errorlist')           # the form flags the missing appointment
        self.assertEqual(Invoice.objects.count(), 0)     # nothing was billed

    def test_submitting_with_an_appointment_bills_the_consultation(self):
        resp = self.c.post('/billing/create/', {
            'patient': self.patient.pk, 'appointment': self.appt.pk,
            'payment_method': 'CASH', 'discount': '0', 'paid': '0'})
        self.assertEqual(resp.status_code, 302)
        from billing.models import Invoice
        inv = Invoice.objects.get(appointment=self.appt)
        self.assertEqual(inv.total, 500)
        self.assertTrue(inv.number)                      # got an accounting number
