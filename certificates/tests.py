"""Birth & Death certificates.

    python manage.py test certificates --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import BirthCertificate, DeathCertificate


def _future():
    return date.today() + timedelta(days=365)


class CertificatesTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_issue_birth_certificate(self):
        resp = self._client().post(reverse('birth_create'), {
            'child_name': 'Baby Ali', 'sex': 'M',
            'date_of_birth': date.today().strftime('%Y-%m-%d'),
            'mother_name': 'Ayesha', 'registered_on': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        cert = BirthCertificate.objects.get(child_name='Baby Ali')
        self.assertTrue(cert.serial_no.startswith('B-'))

    def test_issue_death_certificate(self):
        resp = self._client().post(reverse('death_create'), {
            'deceased_name': 'Karim', 'sex': 'M', 'age_text': '70 years',
            'date_of_death': date.today().strftime('%Y-%m-%d'),
            'cause_of_death': 'Cardiac arrest', 'registered_on': date.today().strftime('%Y-%m-%d'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(DeathCertificate.objects.filter(deceased_name='Karim').exists())

    def test_serial_is_per_hospital(self):
        c1 = BirthCertificate.objects.create(sex='M', date_of_birth=date.today(), mother_name='A', hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        c2 = BirthCertificate.objects.create(sex='F', date_of_birth=date.today(), mother_name='B', hospital=other)
        set_current_hospital(self.h)
        self.assertEqual(c1.serial_no, 'B-00001')
        self.assertEqual(c2.serial_no, 'B-00001')   # each hospital numbers from 1

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('certificate_list')).status_code, 403)

    def test_scoped_to_hospital(self):
        DeathCertificate.objects.create(deceased_name='X', sex='M', date_of_death=date.today(),
                                        cause_of_death='y', hospital=self.h)
        other = Hospital.objects.create(name='O2', slug='o2', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(DeathCertificate.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(DeathCertificate.objects.count(), 1)
