"""Panel / Insurance / Sehat Card billing.

    python manage.py test panels --settings=pharma_mgmt.test_settings

Covers: a covered patient's bills auto-attribute to their panel as a claim; an
uncovered patient's do not; OPD for a covered patient is owed by the panel (paid=0);
outstanding = billed − co-pay − panel payments; a payment reduces it; the feature
gate and tenant isolation hold.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from billing.models import Invoice
from billing.services import create_opd_invoice, create_service_invoice
from opd.models import Appointment, Doctor
from patients.models import Patient
from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital

from .models import Panel
from .services import outstanding_for, record_payment


def _future():
    return date.today() + timedelta(days=365)


class PanelBillingTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw',
                                               role='ADMIN', hospital=self.h)
        self.panel = Panel.objects.create(name='Sehat Card Plus', type=Panel.SEHAT_CARD,
                                          hospital=self.h)
        self.covered = Patient.objects.create(full_name='Covered', gender='M',
                                              hospital=self.h, panel=self.panel,
                                              panel_member_id='SC-123')
        self.uncovered = Patient.objects.create(full_name='Uncovered', gender='M',
                                                hospital=self.h)
        self.doctor = Doctor.objects.create(full_name='Dr X', opd_fee=Decimal('500'))

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    # --- auto-attribution -------------------------------------------------
    def test_service_invoice_attributes_covered_patient_to_panel(self):
        inv = create_service_invoice(patient=self.covered, created_by=self.admin,
                                     items=[('Lab: CBC', Decimal('800'))])
        self.assertEqual(inv.panel_id, self.panel.pk)
        self.assertEqual(inv.claim_status, 'PENDING')

    def test_service_invoice_leaves_uncovered_patient_unpanelled(self):
        inv = create_service_invoice(patient=self.uncovered, created_by=self.admin,
                                     items=[('Lab: CBC', Decimal('800'))])
        self.assertIsNone(inv.panel_id)
        self.assertEqual(inv.claim_status, '')

    def test_opd_covered_is_owed_by_panel_uncovered_paid_upfront(self):
        appt_c = Appointment.objects.create(patient=self.covered, doctor=self.doctor)
        inv_c = create_opd_invoice(appt_c, self.admin)
        self.assertEqual(inv_c.panel_id, self.panel.pk)
        self.assertEqual(inv_c.paid, Decimal('0.00'))       # panel owes it

        appt_u = Appointment.objects.create(patient=self.uncovered, doctor=self.doctor)
        inv_u = create_opd_invoice(appt_u, self.admin)
        self.assertIsNone(inv_u.panel_id)
        self.assertEqual(inv_u.paid, inv_u.total)           # patient paid upfront

    # --- ledger maths -----------------------------------------------------
    def test_outstanding_then_payment_reduces_it(self):
        create_service_invoice(patient=self.covered, created_by=self.admin,
                               items=[('Scan', Decimal('1000'))])          # paid=0
        create_service_invoice(patient=self.covered, created_by=self.admin,
                               items=[('Lab', Decimal('500'))], paid=Decimal('100'))
        # billed 1500, co-pay 100 → panel owes 1400
        self.assertEqual(outstanding_for(self.panel), Decimal('1400.00'))
        record_payment(self.panel, Decimal('400'), received_by=self.admin)
        self.assertEqual(outstanding_for(self.panel), Decimal('1000.00'))

    def test_record_payment_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            record_payment(self.panel, Decimal('0'))

    def test_pharmacy_sale_billed_to_panel_adds_to_outstanding(self):
        """A POS sale billed to the panel defaults to unpaid and its balance is
        the panel's receivable — counted in outstanding alongside invoices."""
        from decimal import Decimal as D

        from inventory.models import Medicine
        from sales.services import create_sale
        med = Medicine.objects.create(name='Paracetamol', price=D('50'),
                                      quantity=100, expiry_date=_future(), hospital=self.h)
        sale = create_sale(items=[{'medicine_id': med.id, 'quantity': 4}],
                           patient=self.covered, panel=self.panel, cashier=self.admin)
        self.assertEqual(sale.panel_id, self.panel.pk)
        self.assertEqual(sale.paid, D('0.00'))            # panel owes it
        self.assertEqual(sale.total, D('200.00'))
        self.assertEqual(outstanding_for(self.panel), D('200.00'))

    def test_pharmacy_sale_without_panel_still_paid_in_full(self):
        from decimal import Decimal as D

        from inventory.models import Medicine
        from sales.services import create_sale
        med = Medicine.objects.create(name='Brufen', price=D('30'),
                                      quantity=100, expiry_date=_future(), hospital=self.h)
        sale = create_sale(items=[{'medicine_id': med.id, 'quantity': 2}],
                           patient=self.uncovered, cashier=self.admin)
        self.assertIsNone(sale.panel_id)
        self.assertEqual(sale.paid, sale.total)           # normal cash sale unchanged

    # --- access + isolation ----------------------------------------------
    def test_panel_list_requires_feature(self):
        self.assertEqual(self._client().get(reverse('panel_list')).status_code, 200)
        pharm = User.objects.create_user(email='p@h.com', password='pw',
                                         role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('panel_list')).status_code, 403)

    def test_ledger_renders(self):
        create_service_invoice(patient=self.covered, created_by=self.admin,
                               items=[('Scan', Decimal('1000'))])
        resp = self._client().get(reverse('panel_ledger', args=[self.panel.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Sehat Card Plus')

    def test_panel_scoped_to_hospital(self):
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertFalse(Panel.objects.filter(pk=self.panel.pk).exists())
        set_current_hospital(self.h)
        self.assertTrue(Panel.objects.filter(pk=self.panel.pk).exists())
