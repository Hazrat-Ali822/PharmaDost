"""Hard-delete of a hospital from the SaaS owner portal.

    python manage.py test saas.tests_delete --settings=pharma_mgmt.test_settings

Guards: only a superuser reaches it; the type-the-name gate must match; a match
wipes the tenant's data AND its staff logins (User.hospital is SET_NULL, so they
would otherwise be left as orphaned accounts that can still sign in); another
tenant is left completely untouched.
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from sales.models import Sale
from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital


def _future():
    return date.today() + timedelta(days=365)


class HospitalDeleteTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(email='owner@sehatyar.online', password='pw')
        self.doomed = Hospital.objects.create(name='Doomed Clinic', slug='doomed',
                                              expiry_date=_future())
        self.keep = Hospital.objects.create(name='Safe Clinic', slug='safe',
                                            expiry_date=_future())
        # Data + a staff login in each tenant.
        for h, tag in ((self.doomed, 'D'), (self.keep, 'K')):
            set_current_hospital(h)
            Patient.objects.create(full_name=f'Patient {tag}', gender='M', hospital=h)
            User.objects.create_user(email=f'staff.{tag}@x.com', password='pw',
                                     role='PHARMACIST', hospital=h)
        clear_current_hospital()

    def tearDown(self):
        clear_current_hospital()

    def _client(self):
        c = Client()
        c.force_login(self.owner)
        return c

    def test_non_superuser_cannot_reach_delete(self):
        staff = User.objects.get(email='staff.K@x.com')
        c = Client(); c.force_login(staff)
        resp = c.get(reverse('saas:hospital_delete', args=[self.doomed.pk]))
        self.assertNotEqual(resp.status_code, 200)  # redirected to login
        self.assertTrue(Hospital.objects.filter(pk=self.doomed.pk).exists())

    def test_wrong_name_deletes_nothing(self):
        c = self._client()
        resp = c.post(reverse('saas:hospital_delete', args=[self.doomed.pk]),
                      {'confirm_name': 'wrong name'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Hospital.objects.filter(pk=self.doomed.pk).exists())
        self.assertTrue(Patient.objects.filter(hospital=self.doomed).exists())

    def test_correct_name_wipes_hospital_data_and_staff(self):
        c = self._client()
        resp = c.post(reverse('saas:hospital_delete', args=[self.doomed.pk]),
                      {'confirm_name': 'Doomed Clinic'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Doomed tenant is gone, root and all.
        self.assertFalse(Hospital.objects.filter(pk=self.doomed.pk).exists())
        self.assertFalse(Patient.objects.filter(full_name='Patient D').exists())
        self.assertFalse(User.objects.filter(email='staff.D@x.com').exists())
        # The other tenant is completely untouched.
        self.assertTrue(Hospital.objects.filter(pk=self.keep.pk).exists())
        self.assertTrue(Patient.objects.filter(full_name='Patient K').exists())
        self.assertTrue(User.objects.filter(email='staff.K@x.com').exists())
        # The owner's own account survives.
        self.assertTrue(User.objects.filter(email='owner@sehatyar.online').exists())

    def test_delete_tenant_that_has_traded(self):
        """Regression: a tenant with sales/invoices/stock has child rows holding
        PROTECT FKs to Medicine/Patient/Customer, so a bare hospital.delete()
        raised ProtectedError. purge_tenant must clear it in passes and the
        delete must succeed — leaving the other tenant untouched."""
        from decimal import Decimal

        from inventory.models import Medicine
        from billing.models import Invoice
        from billing.services import create_service_invoice
        from sales.models import Sale
        from sales.services import create_sale

        h = self.doomed
        set_current_hospital(h)
        patient = Patient.objects.get(full_name='Patient D')
        med = Medicine.objects.create(name='Panadol', price=Decimal('10'), quantity=100,
                                      expiry_date=_future(), hospital=h)
        sale = create_sale(items=[{'medicine_id': med.id, 'quantity': 2}],
                           cashier=self.owner)          # SaleItem → Medicine (PROTECT)
        inv = create_service_invoice(patient=patient, created_by=self.owner,
                                     items=[('Consult', Decimal('50'))])  # Invoice → Patient
        clear_current_hospital()

        c = self._client()
        resp = c.post(reverse('saas:hospital_delete', args=[h.pk]),
                      {'confirm_name': 'Doomed Clinic'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Hospital.objects.filter(pk=h.pk).exists())
        self.assertFalse(Medicine.objects.filter(pk=med.pk).exists())
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
        self.assertFalse(Invoice.objects.filter(pk=inv.pk).exists())
        # Other tenant survives intact.
        self.assertTrue(Hospital.objects.filter(pk=self.keep.pk).exists())
        self.assertTrue(Patient.objects.filter(full_name='Patient K').exists())

    def test_confirm_page_lists_counts(self):
        c = self._client()
        resp = c.get(reverse('saas:hospital_delete', args=[self.doomed.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Doomed Clinic')
        self.assertContains(resp, 'Permanently delete')
