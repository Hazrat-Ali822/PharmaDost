"""Referral in/out + printable letter.

    python manage.py test referral --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import Referral


def _future():
    return date.today() + timedelta(days=365)


class ReferralTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Sana', gender='F', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_create_referral(self):
        resp = self._client().post(reverse('referral_create'), {
            'patient': self.patient.id, 'direction': 'OUT', 'facility': 'LRH Peshawar',
            'reason': 'Neurosurgery opinion', 'urgency': 'URGENT', 'status': 'PENDING',
            'referral_date': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Referral.objects.filter(patient=self.patient, facility='LRH Peshawar').exists())

    def test_letter_renders(self):
        ref = Referral.objects.create(patient=self.patient, facility='LRH', reason='CT', hospital=self.h)
        resp = self._client().get(reverse('referral_letter', args=[ref.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Referral Letter')

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('referral_list')).status_code, 403)

    def test_scoped_to_hospital(self):
        Referral.objects.create(patient=self.patient, facility='X', reason='y', hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(Referral.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(Referral.objects.count(), 1)
