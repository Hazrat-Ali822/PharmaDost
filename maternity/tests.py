"""Maternity — ANC, deliveries, birth register.

    python manage.py test maternity --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import Birth, Delivery, Pregnancy


def _future():
    return date.today() + timedelta(days=365)


class MaternityTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.mother = Patient.objects.create(full_name='Ayesha', gender='F', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_edd_is_lmp_plus_280_days(self):
        lmp = date(2026, 1, 1)
        p = Pregnancy.objects.create(mother=self.mother, lmp=lmp, hospital=self.h)
        self.assertEqual(p.edd, lmp + timedelta(days=280))

    def test_no_lmp_gives_no_edd(self):
        p = Pregnancy.objects.create(mother=self.mother, hospital=self.h)
        self.assertIsNone(p.edd)
        self.assertIsNone(p.gestation_weeks)

    def test_register_anc(self):
        resp = self._client().post(reverse('maternity_register'), {
            'mother': self.mother.id, 'husband_name': 'Ali', 'gravida': 2, 'para': 1,
            'abortions': 0, 'lmp': '2026-01-01',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Pregnancy.objects.filter(mother=self.mother).exists())

    def test_record_delivery_creates_births_and_closes_pregnancy(self):
        preg = Pregnancy.objects.create(mother=self.mother, lmp=date(2026, 1, 1), hospital=self.h)
        resp = self._client().post(reverse('maternity_delivery', args=[preg.pk]), {
            'mother': self.mother.id, 'delivered_at': '2026-10-01T10:00',
            'delivery_type': 'NORMAL', 'outcome': 'LIVE',
            'baby_sex[]': ['F', 'M', ''], 'baby_weight[]': ['3.1', '2.9', ''],
            'baby_status[]': ['ALIVE', 'ALIVE', 'ALIVE'], 'baby_time[]': ['', '', ''],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        d = Delivery.objects.get(mother=self.mother)
        self.assertEqual(d.births.count(), 2)               # twins
        preg.refresh_from_db()
        self.assertEqual(preg.status, 'DELIVERED')

    def test_live_delivery_with_no_baby_rows_makes_one(self):
        resp = self._client().post(reverse('maternity_delivery_new'), {
            'mother': self.mother.id, 'delivered_at': '2026-10-01T10:00',
            'delivery_type': 'CSECTION', 'outcome': 'LIVE',
            'baby_sex[]': ['', '', ''],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Birth.objects.count(), 1)

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('maternity_list')).status_code, 403)

    def test_scoped_to_hospital(self):
        Pregnancy.objects.create(mother=self.mother, hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(Pregnancy.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(Pregnancy.objects.count(), 1)
