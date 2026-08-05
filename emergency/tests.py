"""Emergency / Casualty module.

    python manage.py test emergency --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import EmergencyCase
from .services import register_case


def _future():
    return date.today() + timedelta(days=365)


class EmergencyTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw',
                                               role='ADMIN', hospital=self.h)
        self.p = Patient.objects.create(full_name='Injured', gender='M', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_register_case_optionally_bills(self):
        case = register_case(patient=self.p, created_by=self.admin, triage='RED',
                             chief_complaint='RTA', consultation_fee=Decimal('500'))
        self.assertEqual(case.triage, 'RED')
        self.assertIsNotNone(case.invoice)
        self.assertEqual(case.invoice.total, Decimal('500.00'))

    def test_register_case_without_fee_has_no_invoice(self):
        case = register_case(patient=self.p, created_by=self.admin)
        self.assertIsNone(case.invoice)

    def test_board_sorts_red_first(self):
        register_case(patient=self.p, created_by=self.admin, triage='GREEN')
        register_case(patient=self.p, created_by=self.admin, triage='RED')
        resp = self._client().get(reverse('emergency_board'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertLess(body.index('RED'), body.index('GREEN'))  # RED row rendered first

    def test_intake_creates_new_patient_and_case(self):
        resp = self._client().post(reverse('emergency_intake'), {
            'new_name': 'Walk In', 'new_gender': 'F', 'triage': 'YELLOW',
            'mode_of_arrival': 'AMBULANCE', 'chief_complaint': 'Chest pain',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Patient.objects.filter(full_name='Walk In').exists())
        self.assertTrue(EmergencyCase.objects.filter(chief_complaint='Chest pain').exists())

    def test_intake_requires_a_patient(self):
        resp = self._client().post(reverse('emergency_intake'), {'triage': 'GREEN'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(EmergencyCase.objects.count(), 0)

    def test_disposition_stamps_disposed_at(self):
        case = register_case(patient=self.p, created_by=self.admin)
        self.assertIsNone(case.disposed_at)
        resp = self._client().post(reverse('emergency_detail', args=[case.pk]),
                                   {'disposition': 'DISCHARGED', 'disposition_notes': 'ok'})
        self.assertEqual(resp.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.disposition, 'DISCHARGED')
        self.assertIsNotNone(case.disposed_at)

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw',
                                         role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('emergency_board')).status_code, 403)

    def test_scoped_to_hospital(self):
        register_case(patient=self.p, created_by=self.admin)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(EmergencyCase.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(EmergencyCase.objects.count(), 1)
