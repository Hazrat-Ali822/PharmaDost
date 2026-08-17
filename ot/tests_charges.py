"""An operation bills as its parts, not as one opaque number.

A surgery used to raise a single `standard_charge` line, so the patient could not
read what they were paying for and the hospital could not see which part of an
operation earns. Theatre time, the anaesthetist and the disposables are now each
their own catalogue rate, their own frozen figure on the record, and their own
line on the bill.

    python manage.py test ot.tests_charges --settings=pharma_mgmt.test_settings
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


class ItemisedSurgeryBillTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-charges',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(email='a@charge.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        docuser = User.objects.create_user(email='d@charge.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=docuser, full_name='Ali Raza',
                                            opd_fee=Decimal('500'))
        self.patient = Patient.objects.create(full_name='Surgery Patient',
                                              gender='M', hospital=self.h)
        cat = SurgeryCategory.objects.create(name='General', hospital=self.h)
        self.proc = SurgeryProcedure.objects.create(
            name='Appendectomy', category=cat,
            standard_charge=Decimal('25000'),      # surgeon
            ot_charge=Decimal('5000'),             # theatre
            anesthesia_charge=Decimal('3000'),
            consumables_charge=Decimal('1500'),
            cost_price=Decimal('7000'),
            hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _schedule(self, **extra):
        c = Client()
        c.force_login(self.admin)
        payload = {
            'patient': self.patient.id, 'procedure': self.proc.id,
            'start_time': '2026-08-01T10:00', 'lead_surgeon': self.doctor.id,
            'operation_notes': 'Routine', 'outcome': 'Successful',
        }
        payload.update(extra)
        return c.post(reverse('ot:surgery_create'), payload)

    def test_the_bill_has_a_line_for_each_part(self):
        self.assertEqual(self._schedule().status_code, 302)
        descriptions = [i.description for i in Invoice.objects.get().items.all()]
        self.assertEqual(len(descriptions), 4)
        self.assertTrue(any(d.startswith('OT Surgery:') for d in descriptions))
        self.assertTrue(any(d.startswith('OT Theatre:') for d in descriptions))
        self.assertTrue(any(d.startswith('OT Anaesthesia:') for d in descriptions))
        self.assertTrue(any(d.startswith('OT Consumables:') for d in descriptions))

    def test_the_total_is_the_sum_of_the_parts(self):
        self._schedule()
        self.assertEqual(Invoice.objects.get().total, Decimal('34500'))

    def test_charges_are_prefilled_from_the_catalogue(self):
        self._schedule()
        record = SurgeryRecord.objects.get()
        self.assertEqual(record.surgeon_charge, Decimal('25000'))
        self.assertEqual(record.ot_charge, Decimal('5000'))
        self.assertEqual(record.cost_price, Decimal('7000'))

    def test_a_typed_charge_beats_the_catalogue_rate(self):
        """A long operation uses the theatre longer than the standard rate."""
        self._schedule(ot_charge='9000')
        record = SurgeryRecord.objects.get()
        self.assertEqual(record.ot_charge, Decimal('9000'))
        self.assertEqual(Invoice.objects.get().total, Decimal('38500'))

    def test_a_zero_part_produces_no_line(self):
        """A hospital that does not charge separately for theatre bills exactly
        as it did before this feature."""
        self.proc.ot_charge = Decimal('0')
        self.proc.anesthesia_charge = Decimal('0')
        self.proc.consumables_charge = Decimal('0')
        self.proc.save()
        self._schedule()
        items = Invoice.objects.get().items.all()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].description.startswith('OT Surgery:'))
        self.assertEqual(Invoice.objects.get().total, Decimal('25000'))

    def test_the_charge_freezes_against_a_later_catalogue_change(self):
        self._schedule()
        self.proc.ot_charge = Decimal('99999')
        self.proc.save()
        self.assertEqual(SurgeryRecord.objects.get().ot_charge, Decimal('5000'))

    def test_the_surgeon_line_carries_one_title_not_two(self):
        self._schedule()
        line = Invoice.objects.get().items.filter(
            description__startswith='OT Surgery:').first()
        self.assertIn('Dr. Ali Raza', line.description)
        self.assertNotIn('Dr. Dr.', line.description)

    def test_every_ot_line_is_classified_as_theatre_revenue(self):
        from billing import revenue
        self._schedule()
        for item in Invoice.objects.get().items.all():
            self.assertEqual(revenue.classify(item.description), revenue.OT,
                             item.description)

    def test_ot_profit_is_revenue_minus_the_frozen_cost(self):
        """Cost and revenue must land in the same period. The operation is
        scheduled for a future date but billed today, so both belong to today —
        keying the cost off `start_time` split them across two months."""
        from reports.utils import module_profit_data
        self._schedule()
        today = date.today()          # the bill is raised now, not on the
        rows, _ = module_profit_data(today, today)   # scheduled operation date
        ot = next(r for r in rows if r['key'] == 'OT')
        self.assertEqual(ot['revenue'], Decimal('34500'))
        self.assertEqual(ot['cost'], Decimal('7000'))
        self.assertEqual(ot['profit'], Decimal('27500'))
        self.assertTrue(ot['cost_tracked'])


class SurgeonScopingTest(TestCase):
    """`Doctor` has no hospital column and no TenantManager, so the surgery form
    listed — and would have accepted — every tenant's doctors."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Mine', slug='mine-ot',
                                         expiry_date=_future())
        self.other = Hospital.objects.create(name='Theirs', slug='theirs-ot',
                                             expiry_date=_future())
        self.admin = User.objects.create_user(email='mine@ot.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        mine_user = User.objects.create_user(email='dm@ot.com', password='pw',
                                             role='DOCTOR', hospital=self.h)
        theirs_user = User.objects.create_user(email='dt@ot.com', password='pw',
                                               role='DOCTOR', hospital=self.other)
        self.mine = Doctor.objects.create(user=mine_user, full_name='Mine Doctor')
        self.theirs = Doctor.objects.create(user=theirs_user, full_name='Their Doctor')

    def tearDown(self):
        clear_current_hospital()

    def test_the_form_offers_only_this_hospitals_surgeons(self):
        from ot.forms import SurgeryRecordForm
        offered = SurgeryRecordForm(user=self.admin).fields['lead_surgeon'].queryset
        self.assertIn(self.mine, offered)
        self.assertNotIn(self.theirs, offered)

    def test_posting_another_hospitals_surgeon_is_rejected(self):
        """A ModelChoiceField validates against its own queryset, so the display
        fix and the write fix are the same fix — but assert it, not assume it."""
        patient = Patient.objects.create(full_name='P', gender='M', hospital=self.h)
        cat = SurgeryCategory.objects.create(name='General', hospital=self.h)
        proc = SurgeryProcedure.objects.create(name='Op', category=cat,
                                               standard_charge=Decimal('100'),
                                               hospital=self.h)
        c = Client()
        c.force_login(self.admin)
        resp = c.post(reverse('ot:surgery_create'), {
            'patient': patient.id, 'procedure': proc.id,
            'start_time': '2026-08-01T10:00',
            'lead_surgeon': self.theirs.id,        # not ours
            'operation_notes': 'x', 'outcome': 'Successful',
        })
        self.assertEqual(resp.status_code, 200)     # re-rendered with errors
        self.assertEqual(SurgeryRecord.objects.count(), 0)
