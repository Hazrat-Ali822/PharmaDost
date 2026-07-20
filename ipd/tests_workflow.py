"""Doctor -> reception/OT handoff: advise admission/surgery, then confirm from the queue."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Notification
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor
from ipd.models import Ward, Bed, AdmissionRequest, Admission
from ot.models import SurgeryCategory, SurgeryProcedure, SurgeryRequest, SurgeryRecord


class HandoffWorkflowTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=date.today() + timedelta(days=30))
        self.doc_user = User.objects.create_user(email='d@d.com', password='pw', role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=self.doc_user, full_name='Dr D', opd_fee=Decimal('100'))
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='P One', gender='M', hospital=self.h)
        self.ward = Ward.objects.create(name='Gen', ward_type='General Male',
                                        daily_rate=Decimal('1000'), hospital=self.h)
        self.bed = Bed.objects.create(bed_number='B1', ward=self.ward, status='Available', hospital=self.h)
        cat = SurgeryCategory.objects.create(name='General', hospital=self.h)
        self.proc = SurgeryProcedure.objects.create(name='Appendectomy', category=cat,
                                                    standard_charge=Decimal('20000'), hospital=self.h)

    def test_admission_advise_then_confirm(self):
        c = Client(); c.force_login(self.doc_user)
        r = c.post(reverse('ipd:admission_advise', args=[self.patient.id]),
                   {'reason': 'Needs observation', 'preferred_ward': self.ward.id})
        self.assertEqual(r.status_code, 302)
        ar = AdmissionRequest.objects.get()
        self.assertEqual(ar.status, 'Pending')
        self.assertTrue(Notification.objects.filter(message__icontains='Admission advised').exists())

        # reception/admin confirms from the queue
        c2 = Client(); c2.force_login(self.admin)
        r2 = c2.post(reverse('ipd:admission_create') + f'?request_id={ar.id}', {
            'request_id': ar.id, 'patient': self.patient.id, 'bed': self.bed.id,
            'attending_doctor': self.doctor.id, 'admission_reason': 'Needs observation',
        })
        self.assertEqual(r2.status_code, 302)
        ar.refresh_from_db(); self.bed.refresh_from_db()
        self.assertEqual(ar.status, 'Admitted')
        self.assertIsNotNone(ar.admission_id)
        self.assertEqual(self.bed.status, 'Occupied')
        self.assertEqual(Admission.objects.count(), 1)

    def test_surgery_advise_then_schedule(self):
        c = Client(); c.force_login(self.doc_user)
        r = c.post(reverse('ot:surgery_advise', args=[self.patient.id]),
                   {'reason': 'Acute appendicitis', 'procedure': self.proc.id, 'urgency': 'Urgent'})
        self.assertEqual(r.status_code, 302)
        sr = SurgeryRequest.objects.get()
        self.assertEqual(sr.status, 'Pending')
        self.assertEqual(sr.urgency, 'Urgent')
        self.assertTrue(Notification.objects.filter(message__icontains='Surgery advised').exists())

        c2 = Client(); c2.force_login(self.admin)
        r2 = c2.post(reverse('ot:surgery_create') + f'?request_id={sr.id}', {
            'request_id': sr.id, 'patient': self.patient.id, 'procedure': self.proc.id,
            'start_time': '2026-07-20T10:00', 'lead_surgeon': self.doctor.id,
            'operation_notes': 'Standard appendectomy', 'outcome': 'Successful',
        })
        self.assertEqual(r2.status_code, 302)
        sr.refresh_from_db()
        self.assertEqual(sr.status, 'Scheduled')
        self.assertIsNotNone(sr.surgery_id)
        self.assertEqual(SurgeryRecord.objects.count(), 1)

    def test_pages_render(self):
        c = Client(); c.force_login(self.admin)
        # doctor advise forms
        self.assertEqual(c.get(reverse('ipd:admission_advise', args=[self.patient.id])).status_code, 200)
        self.assertEqual(c.get(reverse('ot:surgery_advise', args=[self.patient.id])).status_code, 200)
        # reception/OT queues
        self.assertEqual(c.get(reverse('ipd:admission_request_list')).status_code, 200)
        self.assertEqual(c.get(reverse('ot:surgery_request_list')).status_code, 200)
        # patient detail (has the new advise buttons)
        self.assertContains(c.get(reverse('patient_detail', args=[self.patient.id])), 'Advise Admission')

    def test_cancel_admission_request(self):
        ar = AdmissionRequest.objects.create(patient=self.patient, advised_by=self.doc_user,
                                             reason='x', hospital=self.h)
        c = Client(); c.force_login(self.admin)
        r = c.post(reverse('ipd:admission_request_cancel', args=[ar.id]))
        self.assertEqual(r.status_code, 302)
        ar.refresh_from_db()
        self.assertEqual(ar.status, 'Cancelled')
