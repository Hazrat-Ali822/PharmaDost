"""Consent forms — template library + signed record.

    python manage.py test consent --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import ConsentForm, ConsentTemplate


def _future():
    return date.today() + timedelta(days=365)


class ConsentTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.doctor = User.objects.create_user(email='d@h.com', password='pw', role='DOCTOR', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Imran', gender='M', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_record_consent_freezes_body(self):
        resp = self._client(self.doctor).post(reverse('consent_create'), {
            'patient': self.patient.id, 'consent_type': 'SURGERY',
            'title': 'Surgery Consent', 'body': 'I consent to the operation.',
            'signed_by': 'Imran', 'signed_on': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        rec = ConsentForm.objects.get(patient=self.patient)
        self.assertEqual(rec.body, 'I consent to the operation.')

    def test_templates_admin_only(self):
        self.assertEqual(self._client(self.doctor).get(reverse('consent_templates')).status_code, 403)
        self.assertEqual(self._client(self.admin).get(reverse('consent_templates')).status_code, 200)

    def test_print_renders(self):
        rec = ConsentForm.objects.create(patient=self.patient, title='X', body='y', hospital=self.h)
        resp = self._client().get(reverse('consent_print', args=[rec.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('consent_list')).status_code, 403)

    def test_scoped_to_hospital(self):
        ConsentTemplate.objects.create(title='T', body='b', hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(ConsentTemplate.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(ConsentTemplate.objects.count(), 1)
