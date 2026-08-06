"""Blood bank — donors, unit inventory, issue.

    python manage.py test bloodbank --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import BloodIssue, BloodUnit


def _future(days=365):
    return date.today() + timedelta(days=days)


class BloodBankTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Nadia', gender='F', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def _unit(self, **kw):
        defaults = dict(bag_number='BAG1', blood_group='O+', donation_date=date.today(),
                        expiry_date=_future(35), hospital=self.h)
        defaults.update(kw)
        return BloodUnit.objects.create(**defaults)

    def test_add_unit_and_dashboard(self):
        self._unit()
        resp = self._client().get(reverse('bloodbank_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'O+')

    def test_issue_marks_unit_issued(self):
        unit = self._unit()
        resp = self._client().post(reverse('bloodbank_issue'), {
            'unit': unit.id, 'patient': self.patient.id,
            'issued_on': date.today().strftime('%Y-%m-%d'), 'cross_match': 'Compatible',
        })
        self.assertEqual(resp.status_code, 302)
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'ISSUED')
        self.assertTrue(BloodIssue.objects.filter(unit=unit, patient=self.patient).exists())

    def test_expired_unit_not_available(self):
        unit = self._unit(bag_number='OLD', expiry_date=date.today() - timedelta(days=1))
        self.assertFalse(unit.is_available)

    def test_feature_gate(self):
        recep = User.objects.create_user(email='r@h.com', password='pw', role='RECEPTIONIST', hospital=self.h)
        self.assertEqual(self._client(recep).get(reverse('bloodbank_dashboard')).status_code, 403)

    def test_scoped_to_hospital(self):
        self._unit()
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(BloodUnit.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(BloodUnit.objects.count(), 1)
