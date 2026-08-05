"""ICD-10 diagnosis coding.

    python manage.py test diagnosis --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import DiagnosisCode, PatientDiagnosis


def _future():
    return date.today() + timedelta(days=365)


class DiagnosisTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.doctor_user = User.objects.create_user(email='d@h.com', password='pw', role='DOCTOR', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M', hospital=self.h)
        self.code = DiagnosisCode.objects.create(code='J18.9', title='Pneumonia', category='Respiratory')

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_seed_icd_populates_catalogue(self):
        call_command('seed_icd')
        self.assertTrue(DiagnosisCode.objects.filter(code='I10').exists())   # hypertension

    def test_add_patient_diagnosis(self):
        resp = self._client().post(reverse('diagnosis_list'), {
            'patient': self.patient.id, 'code': self.code.id,
            'clinical_note': 'CAP', 'diagnosed_on': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(PatientDiagnosis.objects.filter(patient=self.patient, code=self.code).exists())

    def test_catalogue_is_admin_only(self):
        # doctor has the feature but the catalogue is role-gated to ADMIN
        self.assertEqual(self._client(self.doctor_user).get(reverse('diagnosis_catalogue')).status_code, 403)
        self.assertEqual(self._client(self.admin).get(reverse('diagnosis_catalogue')).status_code, 200)

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('diagnosis_list')).status_code, 403)

    def test_patient_diagnosis_scoped_to_hospital(self):
        PatientDiagnosis.objects.create(patient=self.patient, code=self.code, hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(PatientDiagnosis.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(PatientDiagnosis.objects.count(), 1)
