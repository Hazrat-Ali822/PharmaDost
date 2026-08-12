"""Withdrawing an ordered service the patient refused — tests, scans, medicines.

The case this exists for: a doctor orders three lab tests, the patient says they
only want two. Before this, nothing in the app could take the third one back — it
sat Pending in the lab queue for ever and its charge stayed on the bill.

What is asserted here is mostly about the *edges*, because the happy path is easy
and the edges are where money and records go wrong:

  * a cancelled test's charge comes off the invoice, and the other two do not
  * cancelling the last live line VOIDs the invoice rather than leaving a Rs 0 bill
  * money already collected is never silently erased — it surfaces as `refund_due`
  * a test that already has a RESULT cannot be cancelled (the lab did the work)
  * a reason is mandatory, and nothing is ever deleted
  * a medicine cancel touches no invoice at all (medicines bill at dispense time)
  * an emptied prescription leaves the pharmacy's PENDING queue

    python manage.py test tests.test_cancellation --settings=pharma_mgmt.test_settings
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from billing.models import Invoice
from imaging.models import ImagingStudy
from inventory.models import Medicine
from lab.models import LabTest, TestCategory, TestOrder, TestResult
from opd.models import Appointment, Doctor
from patients.models import Patient
from prescriptions.models import Prescription, PrescriptionItem
from saas.models import Hospital


def _exp():
    return date.today() + timedelta(days=365)


class CancelSetup(TestCase):
    """One hospital, one patient, and a 3-test lab order billed at Rs 600."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Cancel Hospital', slug='cancel',
                                         expiry_date=_exp())
        self.admin = User.objects.create_user(email='admin@c.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.labtech = User.objects.create_user(email='lab@c.com', password='pw',
                                                role='LABTECH', hospital=self.h)
        self.recep = User.objects.create_user(email='rec@c.com', password='pw',
                                              role='RECEPTIONIST', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Refusing Patient',
                                              phone='0300', hospital=self.h)

        cat = TestCategory.objects.create(name='Biochem')
        self.t1 = LabTest.objects.create(category=cat, name='CBC', price=Decimal('100'))
        self.t2 = LabTest.objects.create(category=cat, name='LFT', price=Decimal('200'))
        self.t3 = LabTest.objects.create(category=cat, name='RFT', price=Decimal('300'))

        self.client = Client()

    def _order(self, user=None):
        """A 3-test order with its invoice, built the way the live view builds it."""
        from lab.forms import TestOrderCreateForm
        from lab.services import create_test_order
        user = user or self.admin
        form = TestOrderCreateForm(
            {'patient': self.patient.pk,
             'tests': [self.t1.pk, self.t2.pk, self.t3.pk]}, user=user)
        assert form.is_valid(), form.errors
        return create_test_order(form, user)


class LabCancelTest(CancelSetup):

    def test_order_starts_with_three_tests_and_a_600_bill(self):
        order = self._order()
        self.assertEqual(order.results.count(), 3)
        self.assertEqual(order.invoice.total, Decimal('600.00'))
        self.assertEqual(order.invoice.items.count(), 3)

    def test_cancelling_one_test_leaves_the_other_two_and_reduces_the_bill(self):
        order = self._order()
        from lab.services import cancel_test

        rft = order.results.get(lab_test=self.t3)
        cancel_test(rft, user=self.labtech, reason='Patient refused')

        rft.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(rft.is_cancelled)
        self.assertEqual(rft.cancelled_by, self.labtech)
        self.assertEqual(rft.cancel_reason, 'Patient refused')
        # the row is still there — nothing is deleted
        self.assertEqual(order.results.count(), 3)
        self.assertEqual(order.active_results.count(), 2)
        # the order is still live because two tests remain
        self.assertEqual(order.status, 'Pending')
        # Rs 300 came off the bill
        self.assertEqual(order.invoice.items.count(), 2)
        self.assertEqual(order.invoice.total, Decimal('300.00'))
        self.assertEqual(order.invoice.status, 'ACTIVE')
        self.assertEqual(order.total_price, Decimal('300'))

    def test_cancelling_the_last_live_test_cancels_the_order_and_voids_the_bill(self):
        order = self._order()
        from lab.services import cancel_test

        for r in list(order.results.all()):
            cancel_test(r, user=self.admin, reason='Patient refused all')

        order.refresh_from_db()
        self.assertEqual(order.status, 'Cancelled')
        self.assertEqual(order.cancelled_by, self.admin)
        self.assertEqual(order.invoice.status, 'VOID')
        self.assertEqual(order.invoice.total, Decimal('0.00'))
        # and it is gone from the ACTIVE-only default manager
        self.assertFalse(Invoice.objects.filter(pk=order.invoice.pk).exists())
        self.assertTrue(Invoice.all_objects.filter(pk=order.invoice.pk).exists())

    def test_money_already_collected_surfaces_as_a_refund_never_erased(self):
        order = self._order()
        inv = order.invoice
        inv.paid = Decimal('600.00')          # patient paid the full bill at the counter
        inv.save(update_fields=['paid'])

        from lab.services import cancel_test
        money = cancel_test(order.results.get(lab_test=self.t3),
                            user=self.labtech, reason='Refused')

        inv.refresh_from_db()
        self.assertEqual(inv.total, Decimal('300.00'))
        # `paid` is untouched — the day book already counted that cash
        self.assertEqual(inv.paid, Decimal('600.00'))
        self.assertEqual(money['refund_due'], Decimal('300.00'))

    def test_a_test_with_a_result_cannot_be_cancelled(self):
        order = self._order()
        r = order.results.get(lab_test=self.t1)
        r.result_value = '12.4'
        r.save(update_fields=['result_value'])

        from lab.services import cancel_test
        with self.assertRaises(ValidationError):
            cancel_test(r, user=self.labtech, reason='Refused')
        r.refresh_from_db()
        self.assertFalse(r.is_cancelled)
        self.assertEqual(order.invoice.items.count(), 3)   # bill untouched

    def test_a_reason_is_mandatory(self):
        order = self._order()
        from lab.services import cancel_test
        with self.assertRaises(ValidationError):
            cancel_test(order.results.first(), user=self.labtech, reason='   ')

    def test_cancel_whole_order_refuses_when_any_result_is_entered(self):
        order = self._order()
        r = order.results.get(lab_test=self.t2)
        r.result_value = '40'
        r.save(update_fields=['result_value'])

        from lab.services import cancel_order
        with self.assertRaises(ValidationError):
            cancel_order(order, user=self.admin, reason='Refused')
        order.refresh_from_db()
        self.assertEqual(order.status, 'Pending')

    def test_cancel_whole_order_voids_the_bill(self):
        order = self._order()
        from lab.services import cancel_order
        cancel_order(order, user=self.admin, reason='Patient left')

        order.refresh_from_db()
        self.assertEqual(order.status, 'Cancelled')
        self.assertEqual(order.active_results.count(), 0)
        self.assertEqual(order.invoice.status, 'VOID')

    def test_the_ordering_doctor_is_notified(self):
        from accounts.models import Notification
        doctor_user = User.objects.create_user(email='doc@c.com', password='pw',
                                               role='DOCTOR', hospital=self.h)
        order = self._order(user=doctor_user)
        from lab.services import cancel_test
        cancel_test(order.results.get(lab_test=self.t1),
                    user=self.labtech, reason='Patient refused')

        self.assertTrue(Notification.objects.filter(user=doctor_user).exists())

    def test_cancelled_orders_are_not_counted_as_completed_in_the_list(self):
        order = self._order()
        from lab.services import cancel_order
        cancel_order(order, user=self.admin, reason='Refused')

        self.client.force_login(self.admin)
        completed = self.client.get(reverse('lab:order_list') + '?show=completed')
        self.assertNotContains(completed, f'>{order.id}</td>')
        cancelled = self.client.get(reverse('lab:order_list') + '?show=cancelled')
        self.assertContains(cancelled, f'>{order.id}</td>')

    def test_payment_cannot_be_collected_on_a_cancelled_order(self):
        order = self._order()
        from lab.services import cancel_order
        cancel_order(order, user=self.admin, reason='Refused')

        self.client.force_login(self.admin)
        self.client.post(reverse('lab:collect_payment', args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'Pending')

    def test_receptionist_cannot_reach_the_cancel_screen(self):
        """Reception is deliberately outside CANCEL_ROLES — they never have the
        conversation in which the patient refuses a test."""
        order = self._order()
        self.client.force_login(self.recep)
        r = self.client.get(reverse('lab:order_cancel', args=[order.id]))
        self.assertEqual(r.status_code, 403)

    def test_labtech_can_cancel_through_the_view(self):
        order = self._order()
        result = order.results.get(lab_test=self.t3)
        self.client.force_login(self.labtech)
        r = self.client.post(
            reverse('lab:test_cancel', args=[order.id, result.id]),
            {'reason': 'Patient could not afford it'})
        self.assertEqual(r.status_code, 302)
        result.refresh_from_db()
        self.assertTrue(result.is_cancelled)

    def test_cancel_view_is_tenant_scoped(self):
        """Another hospital's order must 404, not cancel."""
        other = Hospital.objects.create(name='Other', slug='other', expiry_date=_exp())
        outsider = User.objects.create_user(email='out@o.com', password='pw',
                                            role='ADMIN', hospital=other)
        order = self._order()
        self.client.force_login(outsider)
        r = self.client.post(reverse('lab:order_cancel', args=[order.id]),
                             {'reason': 'nope'})
        self.assertEqual(r.status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, 'Pending')


class ImagingCancelTest(CancelSetup):

    def _study(self, user=None):
        from imaging.forms import ImagingStudyCreateForm
        from imaging.services import create_study
        user = user or self.admin
        form = ImagingStudyCreateForm(
            {'patient': self.patient.pk, 'modality': 'ULTRASOUND',
             'study_name': 'Abdominal Ultrasound', 'price': '1500',
             'clinical_note': ''}, user=user)
        assert form.is_valid(), form.errors
        return create_study(form, user)

    def test_cancelling_a_scan_voids_its_bill(self):
        study = self._study()
        self.assertEqual(study.invoice.total, Decimal('1500.00'))

        from imaging.services import cancel_study
        cancel_study(study, user=self.admin, reason='Patient refused')

        study.refresh_from_db()
        self.assertEqual(study.status, 'Cancelled')
        self.assertEqual(study.cancel_reason, 'Patient refused')
        self.assertEqual(study.invoice.status, 'VOID')

    def test_a_reported_scan_cannot_be_cancelled(self):
        study = self._study()
        study.findings = 'Normal liver echotexture.'
        study.save(update_fields=['findings'])

        from imaging.services import cancel_study
        with self.assertRaises(ValidationError):
            cancel_study(study, user=self.admin, reason='Refused')
        study.refresh_from_db()
        self.assertEqual(study.status, 'Pending')

    def test_cancelled_studies_leave_the_default_list(self):
        study = self._study()
        from imaging.services import cancel_study
        cancel_study(study, user=self.admin, reason='Refused')

        self.client.force_login(self.admin)
        active = self.client.get(reverse('imaging:study_list'))
        self.assertNotContains(active, 'Abdominal Ultrasound')
        cancelled = self.client.get(reverse('imaging:study_list') + '?show=cancelled')
        self.assertContains(cancelled, 'Abdominal Ultrasound')


class PrescriptionCancelTest(CancelSetup):

    def setUp(self):
        super().setUp()
        self.doctor_user = User.objects.create_user(
            email='rxdoc@c.com', password='pw', role='DOCTOR', hospital=self.h)
        self.pharmacist = User.objects.create_user(
            email='ph@c.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.doctor = Doctor.objects.create(full_name='Dr Rx', user=self.doctor_user)
        self.appt = Appointment.objects.create(patient=self.patient, doctor=self.doctor)
        self.rx = Prescription.objects.create(appointment=self.appt)
        self.m1 = Medicine.objects.create(name='Panadol', price=Decimal('10'),
                                          expiry_date=_exp(), hospital=self.h)
        self.m2 = Medicine.objects.create(name='Brufen', price=Decimal('20'),
                                          expiry_date=_exp(), hospital=self.h)
        self.i1 = PrescriptionItem.objects.create(prescription=self.rx, medicine=self.m1,
                                                  dosage='1+0+1', duration_days=3)
        self.i2 = PrescriptionItem.objects.create(prescription=self.rx, medicine=self.m2,
                                                  dosage='0+0+1', duration_days=5)

    def test_cancelling_one_medicine_touches_no_invoice(self):
        before = Invoice.all_objects.count()
        from prescriptions.services import cancel_item
        cancel_item(self.i2, user=self.pharmacist, reason='Has it at home')

        self.i2.refresh_from_db()
        self.rx.refresh_from_db()
        self.assertTrue(self.i2.is_cancelled)
        self.assertEqual(self.rx.active_items.count(), 1)
        self.assertEqual(self.rx.status, 'PENDING')   # one medicine still to dispense
        self.assertEqual(Invoice.all_objects.count(), before)

    def test_cancelling_every_medicine_takes_the_rx_out_of_the_pending_queue(self):
        from prescriptions.services import cancel_item
        cancel_item(self.i1, user=self.pharmacist, reason='Refused')
        cancel_item(self.i2, user=self.pharmacist, reason='Refused')

        self.rx.refresh_from_db()
        self.assertEqual(self.rx.status, 'CANCELLED')
        self.assertEqual(self.rx.items.count(), 2)    # nothing deleted
        self.assertFalse(
            Prescription.objects.filter(pk=self.rx.pk,
                                        status__in=['PENDING', 'PARTIAL']).exists())

    def test_cancel_whole_prescription(self):
        from prescriptions.services import cancel_prescription
        n = cancel_prescription(self.rx, user=self.doctor_user, reason='Withdrawn')
        self.rx.refresh_from_db()
        self.assertEqual(n, 2)
        self.assertEqual(self.rx.status, 'CANCELLED')
        self.assertEqual(self.rx.cancelled_by, self.doctor_user)

    def test_a_fully_dispensed_prescription_cannot_be_cancelled(self):
        self.rx.status = 'DISPENSED'
        self.rx.save(update_fields=['status'])
        from prescriptions.services import cancel_prescription
        with self.assertRaises(ValidationError):
            cancel_prescription(self.rx, user=self.admin, reason='Refused')

    def test_the_prescriber_is_notified(self):
        from accounts.models import Notification
        from prescriptions.services import cancel_item
        cancel_item(self.i1, user=self.pharmacist, reason='Refused')
        self.assertTrue(Notification.objects.filter(user=self.doctor_user).exists())

    def test_pharmacist_can_reach_the_cancel_screen(self):
        """The pharmacist holds `pos`, not `prescriptions` — the gate has to accept
        either key or the person actually facing the patient cannot do this."""
        self.client.force_login(self.pharmacist)
        r = self.client.get(reverse('rx_item_cancel', args=[self.rx.pk, self.i1.pk]))
        self.assertEqual(r.status_code, 200)

    def test_receptionist_cannot_cancel_a_medicine(self):
        self.client.force_login(self.recep)
        r = self.client.post(reverse('rx_item_cancel', args=[self.rx.pk, self.i1.pk]),
                             {'reason': 'x'})
        self.assertEqual(r.status_code, 403)
        self.i1.refresh_from_db()
        self.assertFalse(self.i1.is_cancelled)

    def test_pos_does_not_preload_a_cancelled_medicine(self):
        from prescriptions.services import cancel_item
        cancel_item(self.i2, user=self.pharmacist, reason='Refused')

        self.client.force_login(self.pharmacist)
        r = self.client.get(reverse('sale_create') + f'?prescription_id={self.rx.pk}')
        self.assertEqual(r.status_code, 200)
        # Both medicines are in the full catalogue dropdown; only the live one may
        # be in the cart JSON the POS pre-loads from the Rx.
        rx_json = json.loads(r.context['rx_items_json'] or '[]')
        loaded = {row['medicine_id'] for row in rx_json}
        self.assertEqual(loaded, {self.m1.pk})
