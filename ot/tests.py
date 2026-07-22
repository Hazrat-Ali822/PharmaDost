"""Operation Theatre: scheduling a surgery must also bill it, atomically.

The handoff (doctor advises -> OT queue -> schedule) is covered in
ipd/tests_workflow.py; this file covers the OT side's own invariants.

    python manage.py test ot --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from billing.models import Invoice
from opd.models import Doctor
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital
from ot.models import SurgeryCategory, SurgeryProcedure, SurgeryRecord


def _future():
    return date.today() + timedelta(days=365)


class SurgeryBillingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        cls.admin = User.objects.create_user(email='a@t.com', password='pw',
                                             role='ADMIN', hospital=cls.h)
        docuser = User.objects.create_user(email='d@t.com', password='pw',
                                           role='DOCTOR', hospital=cls.h)
        cls.doctor = Doctor.objects.create(user=docuser, full_name='Dr Surgeon',
                                           opd_fee=Decimal('500'))
        cls.patient = Patient.objects.create(full_name='Surgery Patient', gender='M',
                                             mrn='OT-1', hospital=cls.h)
        cat = SurgeryCategory.objects.create(name='General', hospital=cls.h)
        cls.proc = SurgeryProcedure.objects.create(name='Appendectomy', category=cat,
                                                   standard_charge=Decimal('25000'),
                                                   hospital=cls.h)

    def tearDown(self):
        clear_current_hospital()

    def _schedule(self, client):
        return client.post(reverse('ot:surgery_create'), {
            'patient': self.patient.id, 'procedure': self.proc.id,
            'start_time': '2026-08-01T10:00', 'lead_surgeon': self.doctor.id,
            'operation_notes': 'Routine', 'outcome': 'Successful',
        })

    def test_scheduling_a_surgery_generates_an_invoice(self):
        c = Client(); c.force_login(self.admin)
        resp = self._schedule(c)
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(SurgeryRecord.objects.count(), 1)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, self.proc.standard_charge)
        self.assertEqual(invoice.patient, self.patient)

    def test_invoice_names_the_procedure_and_surgeon(self):
        c = Client(); c.force_login(self.admin)
        self._schedule(c)
        description = Invoice.objects.get().items.first().description
        self.assertIn('Appendectomy', description)
        self.assertIn('Dr Surgeon', description)

    def test_invalid_surgery_creates_neither_record_nor_invoice(self):
        """The record and its invoice are written in one transaction — a failure
        must not leave a surgery saved but unbilled."""
        c = Client(); c.force_login(self.admin)
        resp = c.post(reverse('ot:surgery_create'), {
            'patient': '', 'procedure': self.proc.id,     # missing patient
            'start_time': '2026-08-01T10:00', 'lead_surgeon': self.doctor.id,
        })
        self.assertEqual(resp.status_code, 200)           # re-rendered with errors
        self.assertEqual(SurgeryRecord.objects.count(), 0)
        self.assertEqual(Invoice.objects.count(), 0)


class SurgeryAccessTest(TestCase):
    """OT is a clinical module — pharmacy staff have no business there."""

    @classmethod
    def setUpTestData(cls):
        cls.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    def test_pharmacist_cannot_reach_ot(self):
        u = User.objects.create_user(email='ph@t.com', password='pw',
                                     role='PHARMACIST', hospital=self.h)
        c = Client(); c.force_login(u)
        self.assertEqual(c.get(reverse('ot:surgery_list')).status_code, 403)

    def test_ot_module_off_blocks_admin(self):
        h = Hospital.objects.create(name='NoOT', slug='no-ot', expiry_date=_future(),
                                    enabled_modules=['pharmacy'])
        admin = User.objects.create_user(email='noot@t.com', password='pw',
                                         role='ADMIN', hospital=h)
        c = Client(); c.force_login(admin)
        self.assertEqual(c.get(reverse('ot:surgery_list')).status_code, 403)
