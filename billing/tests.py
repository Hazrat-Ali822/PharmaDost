from datetime import date
from decimal import Decimal
from django.test import TestCase
from accounts.models import User
from patients.models import Patient
from opd.models import Appointment, Doctor
from billing.services import create_opd_invoice


class BillingFlowTests(TestCase):
    def test_appointment_token_is_auto_generated(self):
        doctor = Doctor.objects.create(full_name='Dr Ali', specialty='General', opd_fee=500, followup_fee=300)
        patient = Patient.objects.create(mrn='MRN-001', full_name='Test Patient', phone='1234567890')

        first = Appointment.objects.create(patient=patient, doctor=doctor, appointment_date=date.today())
        second = Appointment.objects.create(patient=patient, doctor=doctor, appointment_date=date.today())

        self.assertEqual(first.token_no, 1)
        self.assertEqual(second.token_no, 2)

    def test_create_opd_invoice_links_to_appointment_and_fee(self):
        doctor = Doctor.objects.create(full_name='Dr Sana', specialty='Dermatology', opd_fee=800, followup_fee=500)
        patient = Patient.objects.create(mrn='MRN-002', full_name='Another Patient', phone='9876543210')
        appointment = Appointment.objects.create(patient=patient, doctor=doctor, appointment_date=date.today())
        user = User.objects.create_user(email='billing@example.com', password='pass1234')

        invoice = create_opd_invoice(appointment, user)

        self.assertEqual(invoice.patient, patient)
        self.assertEqual(invoice.appointment, appointment)
        self.assertEqual(invoice.total, Decimal('800.00'))
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(invoice.items.first().description, 'OPD Consultation')
