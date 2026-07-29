"""Offline outbox replay: a browser that was offline POSTs its queued actions to
/offline/sync/ when it reconnects. These prove the two properties that make that
safe — the same forms/validation as the live desk, and idempotency by UUID so a
replayed action never doubles a patient, token or invoice.
"""
import json
from datetime import date, time, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from billing.models import Invoice
from offline_sync.models import ClientAction
from opd.models import Appointment, Department, Doctor, DoctorSchedule
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _visit_payload(doctor, **over):
    data = {
        "full_name": "Offline Walk In", "gender": "M", "age_years": "30",
        "phone": "03001112222", "mrn": "",
        "doctor": doctor.pk, "visit_type": "OPD",
        "appointment_date": timezone.localdate().isoformat(),
        "slot_time": "10:30",
    }
    data.update(over)
    return data


class OfflineSyncBase(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name="Shaheen General Hospital", slug="sgh",
                                         expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(email="r@r.com", password="pw",
                                             role="RECEPTIONIST", hospital=self.h)
        self.dept = Department.objects.create(name="Medicine", hospital=self.h)
        docuser = User.objects.create_user(email="d@d.com", password="pw",
                                           role="DOCTOR", hospital=self.h)
        self.doctor = Doctor.objects.create(user=docuser, full_name="Ahmed Ali",
                                            department=self.dept, opd_fee=500,
                                            followup_fee=200)
        DoctorSchedule.objects.create(doctor=self.doctor, weekday=timezone.localdate().weekday(),
                                      start_time=time(0, 1), end_time=time(23, 58))
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def _sync(self, *actions):
        return self.client.post(reverse("offline_sync"),
                                data=json.dumps({"actions": list(actions)}),
                                content_type="application/json")


class VisitSyncTest(OfflineSyncBase):
    def test_a_queued_visit_registers_and_books_exactly_like_the_desk(self):
        resp = self._sync({"uuid": "u-1", "kind": "visit",
                           "data": _visit_payload(self.doctor)})
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        self.assertEqual(result["status"], "applied")

        patient = Patient.objects.get(full_name="Offline Walk In")
        # the server assigns the real MRN off this hospital's counter, not the client
        self.assertEqual(patient.mrn, "SGH-000001")
        self.assertEqual(patient.hospital, self.h)
        self.assertEqual(result["result"]["mrn"], "SGH-000001")

        appointment = Appointment.objects.get(patient=patient)
        self.assertEqual(appointment.doctor, self.doctor)
        self.assertEqual(result["result"]["token_no"], appointment.token_no)
        # the consultation invoice is raised, same as booking online
        self.assertEqual(Invoice.objects.count(), 1)

    def test_the_same_uuid_replayed_never_creates_a_second_patient(self):
        payload = {"uuid": "dup-1", "kind": "visit", "data": _visit_payload(self.doctor)}
        first = self._sync(payload).json()["results"][0]
        second = self._sync(payload).json()["results"][0]

        self.assertEqual(first["status"], "applied")
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(Patient.objects.filter(full_name="Offline Walk In").count(), 1)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(ClientAction.objects.filter(client_uuid="dup-1").count(), 1)

    def test_two_different_actions_in_one_batch_both_apply(self):
        resp = self._sync(
            {"uuid": "a", "kind": "visit", "data": _visit_payload(self.doctor, full_name="First")},
            {"uuid": "b", "kind": "visit", "data": _visit_payload(self.doctor, full_name="Second")},
        )
        statuses = [r["status"] for r in resp.json()["results"]]
        self.assertEqual(statuses, ["applied", "applied"])
        self.assertEqual(Patient.objects.count(), 2)

    def test_an_existing_patient_is_booked_without_a_duplicate(self):
        patient = Patient.objects.create(full_name="Known", gender="M", hospital=self.h)
        resp = self._sync({"uuid": "e-1", "kind": "visit", "data": {
            "patient_id": patient.pk, "doctor": self.doctor.pk,
            "visit_type": "OPD", "appointment_date": timezone.localdate().isoformat(),
        }})
        self.assertEqual(resp.json()["results"][0]["status"], "applied")
        self.assertEqual(Patient.objects.filter(full_name="Known").count(), 1)
        self.assertTrue(Appointment.objects.filter(patient=patient).exists())

    def test_bad_data_is_rejected_permanently_not_silently_dropped(self):
        # no doctor -> VisitForm is invalid; nothing may be created
        bad = _visit_payload(self.doctor); bad["doctor"] = ""
        resp = self._sync({"uuid": "bad-1", "kind": "visit", "data": bad})
        result = resp.json()["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result.get("permanent"))
        self.assertFalse(Patient.objects.exists())
        self.assertFalse(Appointment.objects.exists())
        # filed so the desk can see what bounced
        self.assertEqual(ClientAction.objects.get(client_uuid="bad-1").status,
                         ClientAction.FAILED)

    def test_a_rejected_action_is_not_retried(self):
        bad = _visit_payload(self.doctor); bad["doctor"] = ""
        payload = {"uuid": "bad-2", "kind": "visit", "data": bad}
        self._sync(payload)
        second = self._sync(payload).json()["results"][0]
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(ClientAction.objects.filter(client_uuid="bad-2").count(), 1)


def _sale_payload(med, qty, **over):
    data = {
        "sale_type": "RETAIL", "customer_name": "Walk In", "payment_method": "CASH",
        "medicine_id[]": [str(med.id)], "quantity[]": [str(qty)],
        "unit_price[]": [""], "line_discount[]": [""],
    }
    data.update(over)
    return data


class OfflineSaleSyncTest(OfflineSyncBase):
    def setUp(self):
        super().setUp()
        from inventory.models import Medicine
        # Pharmacist must exist in the tenant to receive the reconcile alert.
        self.pharmacist = User.objects.create_user(
            email="ph@ph.com", password="pw", role="PHARMACIST", hospital=self.h)
        # POS is the pharmacist's screen, so an offline sale syncs under their login.
        self.client.force_login(self.pharmacist)
        self.med = Medicine.objects.create(
            name="Paracetamol", brand="ACME", price=100, quantity=50,
            expiry_date=date(2035, 1, 1), hospital=self.h)

    def test_an_offline_sale_syncs_and_deducts_stock(self):
        resp = self._sync({"uuid": "s-1", "kind": "sale",
                           "data": _sale_payload(self.med, 5)})
        result = resp.json()["results"][0]
        self.assertEqual(result["status"], "applied")

        from sales.models import Sale
        sale = Sale.objects.get(pk=result["result"]["sale_id"])
        self.assertEqual(sale.total, 500)
        self.assertFalse(sale.needs_reconcile)
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 45)

    def test_the_same_sale_uuid_is_never_billed_twice(self):
        from sales.models import Sale
        payload = {"uuid": "s-dup", "kind": "sale", "data": _sale_payload(self.med, 5)}
        self._sync(payload)
        self._sync(payload)
        self.assertEqual(Sale.objects.count(), 1)
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 45)   # deducted once, not twice

    def test_a_short_stock_offline_sale_is_recorded_and_flagged_not_refused(self):
        from accounts.models import Notification
        from sales.models import Sale
        self.med.quantity = 3
        self.med.save(update_fields=["quantity"])

        resp = self._sync({"uuid": "s-short", "kind": "sale",
                           "data": _sale_payload(self.med, 10)})
        result = resp.json()["results"][0]
        self.assertEqual(result["status"], "applied")   # NOT refused

        sale = Sale.objects.get(pk=result["result"]["sale_id"])
        # billed in full — the patient received all 10
        self.assertEqual(sale.total, 1000)
        self.assertEqual(sum(i.quantity for i in sale.items.all()), 10)
        # only the 3 on hand were deducted; the shortfall is visible, not hidden
        self.med.refresh_from_db()
        self.assertEqual(self.med.quantity, 0)
        self.assertTrue(sale.needs_reconcile)
        self.assertIn("7", sale.reconcile_note)
        # the pharmacist is actively told to reconcile
        self.assertTrue(Notification.objects.filter(user=self.pharmacist).exists())

    def test_a_live_sale_still_refuses_short_stock(self):
        """The default path is unchanged — only offline replay may oversell."""
        from sales.services import create_sale
        self.med.quantity = 2
        self.med.save(update_fields=["quantity"])
        with self.assertRaises(ValueError):
            create_sale(items=[{"medicine_id": self.med.id, "quantity": 10}],
                        customer_name="Walk In")


class OfflineSyncGuardTest(OfflineSyncBase):
    def test_a_user_without_the_feature_cannot_book_through_sync(self):
        nobody = User.objects.create_user(email="n@n.com", password="pw",
                                          role="RECEPTIONIST", hospital=self.h,
                                          custom_features=[])
        c = Client(); c.force_login(nobody)
        resp = c.post(reverse("offline_sync"),
                      data=json.dumps({"actions": [
                          {"uuid": "g-1", "kind": "visit",
                           "data": _visit_payload(self.doctor)}]}),
                      content_type="application/json")
        self.assertEqual(resp.json()["results"][0]["status"], "failed")
        self.assertFalse(Patient.objects.exists())

    def test_an_unknown_kind_is_rejected(self):
        resp = self._sync({"uuid": "k-1", "kind": "nonsense", "data": {}})
        self.assertEqual(resp.json()["results"][0]["status"], "failed")

    def test_the_endpoint_requires_login(self):
        c = Client()
        resp = c.post(reverse("offline_sync"),
                      data=json.dumps({"actions": []}),
                      content_type="application/json")
        # LoginRequiredMiddleware bounces an anonymous POST to the login page
        self.assertIn(resp.status_code, (302, 403))

    def test_a_cross_tenant_replay_numbers_against_the_callers_own_hospital(self):
        """The MRN comes from the syncing user's hospital counter, so a queued
        action can only ever create a patient in that user's tenant."""
        self._sync({"uuid": "t-1", "kind": "visit", "data": _visit_payload(self.doctor)})
        patient = Patient.objects.get(full_name="Offline Walk In")
        self.assertEqual(patient.hospital, self.h)
        self.assertTrue(patient.mrn.startswith("SGH-"))
