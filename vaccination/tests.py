"""Vaccination / EPI records.

    python manage.py test vaccination --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import Vaccine, VaccinationRecord


def _future():
    return date.today() + timedelta(days=365)


class VaccinationTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(email='n@h.com', password='pw', role='NURSE', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Baby Zoya', gender='F', hospital=self.h)
        self.vaccine = Vaccine.objects.create(code='BCG', name='BCG', sequence=0)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_seed_epi(self):
        call_command('seed_epi')
        self.assertTrue(Vaccine.objects.filter(code='PENTA1').exists())

    def test_record_vaccination(self):
        resp = self._client(self.nurse).post(reverse('vaccination_list'), {
            'patient': self.patient.id, 'vaccine': self.vaccine.id, 'dose_number': 1,
            'date_given': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(VaccinationRecord.objects.filter(patient=self.patient, vaccine=self.vaccine).exists())

    def test_catalogue_admin_only(self):
        self.assertEqual(self._client(self.nurse).get(reverse('vaccine_catalogue')).status_code, 403)
        self.assertEqual(self._client(self.admin).get(reverse('vaccine_catalogue')).status_code, 200)

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('vaccination_list')).status_code, 403)

    def test_scoped_to_hospital(self):
        VaccinationRecord.objects.create(patient=self.patient, vaccine=self.vaccine, hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(VaccinationRecord.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(VaccinationRecord.objects.count(), 1)
