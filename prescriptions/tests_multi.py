"""Prescription screen: many medicines + lab tests + imaging, all in one submit."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from opd.models import Doctor, Appointment
from inventory.models import Medicine
from lab.models import TestCategory, LabTest, TestOrder
from imaging.models import ImagingStudy, ScanType
from billing.models import Invoice
from prescriptions.models import Prescription


class PrescriptionMultiTest(TestCase):
    def setUp(self):
        self.doctor_user = User.objects.create_user(email='d@t.com', password='pw', role='DOCTOR')
        self.patient = Patient.objects.create(full_name='Multi Patient', gender='M')
        self.doctor = Doctor.objects.create(full_name='Dr Who', user=self.doctor_user, opd_fee=Decimal('500'))
        self.appt = Appointment.objects.create(patient=self.patient, doctor=self.doctor)
        exp = date.today() + timedelta(days=365)
        self.m1 = Medicine.objects.create(name='Panadol', price=Decimal('10'), expiry_date=exp)
        self.m2 = Medicine.objects.create(name='Brufen', price=Decimal('15'), expiry_date=exp)
        cat = TestCategory.objects.create(name='Blood')
        self.t1 = LabTest.objects.create(category=cat, name='CBC', price=Decimal('300'))
        self.t2 = LabTest.objects.create(category=cat, name='LFT', price=Decimal('600'))
        self.scan = ScanType.objects.create(modality='ULTRASOUND', name='Abdomen US', price=Decimal('1500'))

    def _post(self):
        c = Client()
        c.force_login(self.doctor_user)
        data = {
            'complaint': 'Fever', 'diagnosis': 'Viral', 'notes': 'rest',
            # two medicines
            'meds-TOTAL_FORMS': '2', 'meds-INITIAL_FORMS': '0',
            'meds-MIN_NUM_FORMS': '0', 'meds-MAX_NUM_FORMS': '1000',
            'meds-0-medicine': self.m1.id, 'meds-0-dosage': '1x3', 'meds-0-duration_days': '5', 'meds-0-instructions': 'after meal',
            'meds-1-medicine': self.m2.id, 'meds-1-dosage': '1x2', 'meds-1-duration_days': '3', 'meds-1-instructions': '',
            # two lab tests
            'tests': [self.t1.id, self.t2.id],
            # one scan from the catalog
            'scans': [self.scan.id],
        }
        return c.post(reverse('prescription_create', args=[self.appt.id]), data)

    def test_creates_meds_tests_and_imaging_with_bills(self):
        r = self._post()
        self.assertEqual(r.status_code, 302)  # redirect to patient detail

        presc = Prescription.objects.get()
        self.assertEqual(presc.items.count(), 2)                      # two medicines

        order = TestOrder.objects.get()
        self.assertEqual(order.results.count(), 2)                    # two tests in one order

        study = ImagingStudy.objects.get()
        self.assertEqual(study.modality, 'ULTRASOUND')
        self.assertEqual(study.study_name, 'Abdomen US')
        self.assertEqual(study.price, Decimal('1500'))
        self.assertEqual(study.referred_by, self.doctor_user)

        # a pending bill for lab (300+600=900) and one for imaging (1500)
        totals = sorted(float(i.total) for i in Invoice.objects.all())
        self.assertEqual(totals, [900.0, 1500.0])

    def test_medicine_without_dosage_still_saves_everything(self):
        """Regression: a medicine picked with no dosage used to silently fail the whole
        form, so tests/scans never got created. Now it saves (dosage optional)."""
        c = Client()
        c.force_login(self.doctor_user)
        data = {
            'complaint': 'fever', 'diagnosis': '', 'notes': '',
            'meds-TOTAL_FORMS': '1', 'meds-INITIAL_FORMS': '0',
            'meds-MIN_NUM_FORMS': '0', 'meds-MAX_NUM_FORMS': '1000',
            'meds-0-medicine': self.m1.id, 'meds-0-dosage': '', 'meds-0-duration_days': '', 'meds-0-instructions': '',
            'tests': [self.t1.id], 'scans': [self.scan.id],
        }
        r = c.post(reverse('prescription_create', args=[self.appt.id]), data)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Prescription.objects.get().items.count(), 1)
        self.assertEqual(TestOrder.objects.count(), 1)       # test still ordered
        self.assertEqual(ImagingStudy.objects.count(), 1)    # scan still ordered

    def test_no_tests_or_scans_selected(self):
        c = Client()
        c.force_login(self.doctor_user)
        data = {
            'complaint': '', 'diagnosis': '', 'notes': '',
            'meds-TOTAL_FORMS': '1', 'meds-INITIAL_FORMS': '0',
            'meds-MIN_NUM_FORMS': '0', 'meds-MAX_NUM_FORMS': '1000',
            'meds-0-medicine': self.m1.id, 'meds-0-dosage': '1x1', 'meds-0-duration_days': '2', 'meds-0-instructions': '',
        }
        r = c.post(reverse('prescription_create', args=[self.appt.id]), data)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Prescription.objects.get().items.count(), 1)
        self.assertEqual(ImagingStudy.objects.count(), 0)  # no scan ticked
        self.assertEqual(TestOrder.objects.count(), 0)     # no tests ticked
