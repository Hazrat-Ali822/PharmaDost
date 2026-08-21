import uuid
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor, Appointment
from prescriptions.models import Prescription, PrescriptionItem
from inventory.models import Medicine


class PatientPortalTests(TestCase):
    def setUp(self):
        exp = date.today() + timedelta(days=365)
        self.hospital = Hospital.objects.create(name='City Care Hospital', slug='city-care', expiry_date=exp)
        self.user = User.objects.create_user(email='admin@citycare.com', password='pw', hospital=self.hospital)
        self.patient = Patient.objects.create(
            full_name='Zahid Ahmed',
            gender='M',
            age_years=35,
            phone='03001234567',
            hospital=self.hospital
        )
        self.doc = Doctor.objects.create(full_name='Dr. Shariq Khan', hospital=self.hospital)
        self.appt = Appointment.objects.create(patient=self.patient, doctor=self.doc)
        self.rx = Prescription.objects.create(appointment=self.appt, diagnosis='Acute Pharyngitis', notes='Warm salt gargles')
        self.med = Medicine.objects.create(
            name='Tab Paracetamol 500mg',
            price=Decimal('50.00'),
            expiry_date=date.today() + timedelta(days=180),
            hospital=self.hospital
        )
        PrescriptionItem.objects.create(prescription=self.rx, medicine=self.med, dosage='1x TDS', duration_days=5)

        from lab.models import TestCategory, LabTest, TestOrder, TestResult
        self.cat = TestCategory.objects.create(name='Hematology', hospital=self.hospital)
        self.test = LabTest.objects.create(category=self.cat, name='Hemoglobin', price=Decimal('300.00'), hospital=self.hospital)
        self.lab_order = TestOrder.objects.create(patient=self.patient, status='Completed')
        self.result = TestResult.objects.create(test_order=self.lab_order, lab_test=self.test, result_value='14.2', normal_range='12-16', unit='g/dL')

    def test_anonymous_access_with_portal_token(self):
        c = Client()
        url = reverse('patient_portal_hub', args=[self.patient.portal_token])
        r = c.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Zahid Ahmed')
        self.assertContains(r, 'City Care Hospital')
        self.assertContains(r, 'Acute Pharyngitis')
        self.assertContains(r, 'Tab Paracetamol 500mg')

    def test_invalid_token_returns_404(self):
        c = Client()
        random_token = uuid.uuid4()
        url = reverse('patient_portal_hub', args=[random_token])
        r = c.get(url)
        self.assertEqual(r.status_code, 404)

    def test_portal_lookup_by_phone(self):
        c = Client()
        url = reverse('patient_portal_lookup')
        r = c.get(url)
        self.assertEqual(r.status_code, 200)

        # GET with phone number
        r2 = c.get(url, {'query': '03001234567'})
        self.assertRedirects(r2, reverse('patient_portal_hub', args=[self.patient.portal_token]))

