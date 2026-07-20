"""Cross-tenant isolation: hospital B must never see hospital A's prescriptions."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor, Appointment
from prescriptions.models import Prescription


class PrescriptionTenantIsolationTest(TestCase):
    def setUp(self):
        exp = date.today() + timedelta(days=365)
        self.hA = Hospital.objects.create(name='Hospital A', slug='hosp-a', expiry_date=exp)
        self.hB = Hospital.objects.create(name='Hospital B', slug='hosp-b', expiry_date=exp)

        # A user + patient + prescription that belong to hospital A
        self.adminA = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.hA)
        self.patientA = Patient.objects.create(full_name='Alice A', gender='F', hospital=self.hA)
        self.docA = Doctor.objects.create(full_name='Dr A', opd_fee=Decimal('500'))
        self.apptA = Appointment.objects.create(patient=self.patientA, doctor=self.docA)
        self.rxA = Prescription.objects.create(appointment=self.apptA, diagnosis='Flu (A)')

        # An admin that belongs to hospital B
        self.adminB = User.objects.create_user(email='b@b.com', password='pw', role='ADMIN', hospital=self.hB)

    def test_list_hides_other_hospital(self):
        c = Client(); c.force_login(self.adminB)
        r = c.get(reverse('prescription_list'))
        self.assertNotContains(r, 'Flu (A)')
        self.assertNotContains(r, 'Alice A')

    def test_detail_blocks_other_hospital(self):
        c = Client(); c.force_login(self.adminB)
        r = c.get(reverse('prescription_detail', args=[self.rxA.id]))
        self.assertEqual(r.status_code, 404)

    def test_edit_blocks_other_hospital(self):
        c = Client(); c.force_login(self.adminB)
        r = c.get(reverse('prescription_edit', args=[self.rxA.id]))
        self.assertEqual(r.status_code, 404)

    def test_owner_still_sees_it(self):
        c = Client(); c.force_login(self.adminA)
        r = c.get(reverse('prescription_detail', args=[self.rxA.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Flu (A)')

    def test_hospital_less_nonsuperuser_cannot_see_other_hospital(self):
        """The real-world leak: a non-superuser whose `hospital` was never set must
        still NOT see another hospital's prescriptions (fail closed, not fail open)."""
        orphan = User.objects.create_user(email='orphan@x.com', password='pw', role='ADMIN')  # hospital=None
        c = Client(); c.force_login(orphan)
        # scoped out entirely — blocked (404) rather than served across tenants
        self.assertEqual(c.get(reverse('prescription_detail', args=[self.rxA.id])).status_code, 404)
        self.assertNotContains(c.get(reverse('prescription_list')), 'Flu (A)')

    def test_pos_prefill_blocks_other_hospital(self):
        """POS ?prescription_id= must not load another hospital's prescription."""
        c = Client(); c.force_login(self.adminB)
        r = c.get(reverse('sale_create') + f'?prescription_id={self.rxA.id}')
        # B must not get A's patient pre-selected; a 404 or a clean POS with no
        # pre-selected patient are both acceptable — leaking A's data is not.
        self.assertNotContains(r, 'Alice A', status_code=r.status_code)
