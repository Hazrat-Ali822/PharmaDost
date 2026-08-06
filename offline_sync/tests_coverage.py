"""Every offline `kind` proved end-to-end, through the real sync endpoint.

The point of this module is that a broken handler cannot ship quietly. A handler
that imports a form under the wrong name, or a template that forgets the parent
id its handler needs, fails as a *transient* error — nothing is recorded, the
client retries for ever, and the staff watch a badge that says "waiting to sync"
about an entry that will never sync. That is indistinguishable from working,
which is exactly how six broken kinds survived in the tree.

So `test_every_registered_kind_actually_applies` walks `HANDLERS` itself: adding
a kind without adding a payload here fails the suite.
"""
import json
from datetime import date, time, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from offline_sync.handlers import HANDLERS
from offline_sync.models import ClientAction
from saas.models import Hospital
from saas.utils import clear_current_hospital


class AllKindsBase(TestCase):
    """One tenant with enough of everything for each handler to have something
    real to attach to."""

    def setUp(self):
        from customers.models import Customer
        from imaging.models import ImagingStudy, ScanType
        from inventory.models import Medicine
        from ipd.models import Admission, Bed, Ward
        from lab.models import LabTest, TestCategory, TestOrder, TestResult
        from opd.models import Appointment, Department, Doctor
        from ot.models import SurgeryCategory, SurgeryProcedure
        from patients.models import Patient
        from suppliers.models import Supplier

        self.h = Hospital.objects.create(
            name="Shaheen General Hospital", slug="sgh",
            expiry_date=date.today() + timedelta(days=30))
        # ADMIN holds every feature, so one login can exercise every handler.
        self.user = User.objects.create_user(
            email="admin@sgh.com", password="pw", role="ADMIN", hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

        self.dept = Department.objects.create(name="Medicine", hospital=self.h)
        self.doctor = Doctor.objects.create(
            full_name="Ahmed Ali", department=self.dept, opd_fee=500,
            followup_fee=200, share_percent=50)
        self.patient = Patient.objects.create(
            full_name="Zainab Bibi", gender="F", age_years=30, hospital=self.h)
        self.appointment = Appointment.objects.create(
            patient=self.patient, doctor=self.doctor,
            appointment_date=timezone.localdate(), visit_type="OPD")

        self.medicine = Medicine.objects.create(
            name="Panadol", brand="GSK", price=Decimal("20"), quantity=100,
            expiry_date=date(2035, 1, 1), hospital=self.h)
        self.batch = self.medicine.batches.create(
            quantity=100, cost_price=Decimal("10"), expiry_date=date(2035, 1, 1),
            hospital=self.h)

        self.supplier = Supplier.objects.create(name="Ali Traders", hospital=self.h)
        self.customer = Customer.objects.create(name="Noor Medical", hospital=self.h)

        self.ward = Ward.objects.create(
            name="General", ward_type="General Male", daily_rate=Decimal("1500"),
            hospital=self.h)
        self.bed = Bed.objects.create(bed_number="B-1", ward=self.ward,
                                      status="Available", hospital=self.h)
        self.free_bed = Bed.objects.create(bed_number="B-2", ward=self.ward,
                                           status="Available", hospital=self.h)
        occupied = Bed.objects.create(bed_number="B-9", ward=self.ward,
                                      status="Occupied", hospital=self.h)
        self.admission = Admission.objects.create(
            patient=self.patient, bed=occupied, attending_doctor=self.doctor,
            admission_reason="Observation", hospital=self.h)

        self.cat = TestCategory.objects.create(name="Haematology")
        self.lab_test = LabTest.objects.create(
            category=self.cat, name="CBC", price=Decimal("400"))
        self.order = TestOrder.objects.create(patient=self.patient)
        self.result_row = TestResult.objects.create(
            test_order=self.order, lab_test=self.lab_test)

        self.scan_type = ScanType.objects.create(
            name="Abdomen US", modality="ULTRASOUND", price=Decimal("1200"))
        self.study = ImagingStudy.objects.create(
            patient=self.patient, modality="ULTRASOUND", study_name="Abdomen US",
            price=Decimal("1200"))

        self.surg_cat = SurgeryCategory.objects.create(name="General", hospital=self.h)
        self.procedure = SurgeryProcedure.objects.create(
            name="Appendectomy", category=self.surg_cat,
            standard_charge=Decimal("30000"), hospital=self.h)

        # clinical add-ons (parents for the antenatal/vaccination/diagnosis kinds)
        from maternity.models import Pregnancy
        from vaccination.models import Vaccine
        from diagnosis.models import DiagnosisCode
        self.pregnancy = Pregnancy.objects.create(
            mother=self.patient, lmp=date.today() - timedelta(days=90), hospital=self.h)
        self.vaccine = Vaccine.objects.create(code="BCG", name="BCG")          # global
        self.dx_code = DiagnosisCode.objects.create(code="J06.9", title="Acute URTI")  # global

    def tearDown(self):
        clear_current_hospital()

    def _sync(self, kind, data, uuid):
        resp = self.client.post(
            reverse("offline_sync"),
            data=json.dumps({"actions": [{"uuid": uuid, "kind": kind, "data": data}]}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["results"][0]

    def _apply(self, kind, data, uuid=None):
        """Sync one action and insist it applied — printing the server's reason
        when it did not, because 'error' alone is useless to whoever reads this."""
        res = self._sync(kind, data, uuid or f"u-{kind}")
        self.assertEqual(
            res["status"], "applied",
            f"kind '{kind}' did not apply: {res.get('error')!r}")
        return res["result"]

    # ---- the payloads, exactly as each template posts them ----------------
    def payloads(self):
        today = timezone.localdate().isoformat()
        now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        return {
            "visit": {
                "full_name": "Offline Walk In", "gender": "M", "age_years": "40",
                "phone": "03001112222", "mrn": "",
                "doctor": self.doctor.pk, "visit_type": "OPD",
                "appointment_date": today, "slot_time": "10:30",
            },
            "patient": {"full_name": "Queued Patient", "gender": "F",
                        "age_years": "25", "mrn": ""},
            "patient_record": {
                "patient_id": self.patient.pk, "record_type": "CONSULT",
                "date": today, "title": "Follow-up", "diagnosis": "Viral fever",
                "details": "", "bp": "", "pulse": "", "temperature": "", "weight": "",
            },
            "appointment": {
                "patient": self.patient.pk, "doctor": self.doctor.pk,
                "appointment_date": today, "slot_time": "11:00", "token_no": "1",
                "visit_type": "OPD", "status": "BOOKED",
            },
            "department": {"name": "Cardiology", "description": "", "is_active": "on"},
            "doctor": {
                "full_name": "Dr Offline", "department": self.dept.pk,
                "specialty": "GP", "pmdc_no": "", "opd_fee": "600",
                "followup_fee": "300", "followup_valid_days": "7",
                "share_percent": "40",
            },
            "payout": {"doctor_id": self.doctor.pk, "date": today,
                       "amount": "5000", "payment_method": "CASH", "note": ""},
            "prescription": {
                "appointment_id": self.appointment.pk,
                "complaint": "Fever", "diagnosis": "Viral", "notes": "",
                "meds-TOTAL_FORMS": "1", "meds-INITIAL_FORMS": "0",
                "meds-MIN_NUM_FORMS": "0", "meds-MAX_NUM_FORMS": "1000",
                "meds-0-medicine": str(self.medicine.pk),
                "meds-0-custom_medicine_name": "",
                "meds-0-dosage": "1+0+1", "meds-0-duration_days": "5",
                "meds-0-instructions": "After meals",
            },
            "rx_preset": {
                "name": "Fever pack", "description": "",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
                "items-0-medicine": str(self.medicine.pk),
                "items-0-dosage": "1+0+1", "items-0-duration_days": "3",
                "items-0-instructions": "",
            },
            "sale": {
                "sale_type": "RETAIL", "customer_name": "Walk In",
                "payment_method": "CASH", "discount": "0", "paid": "40",
                "medicine_id[]": [str(self.medicine.pk)], "quantity[]": ["2"],
                "unit_price[]": [""], "line_discount[]": [""],
            },
            "medicine": {
                "name": "Brufen", "generic_name": "Ibuprofen", "brand": "Abbott",
                "manufacturer": "", "category": "TABLET", "barcode": "",
                "pack_size": "", "units_per_pack": "1", "rack_location": "",
                "price": "30", "wholesale_price": "25", "reorder_level": "10",
                "quantity": "0", "expiry_date": "2035-06-30", "supplier": "",
            },
            "adjustment": {"batch": self.batch.pk, "qty_change": "-5",
                           "reason": "DAMAGE", "notes": "broken strip"},
            "purchase_return": {
                "supplier": self.supplier.pk, "reason": "EXPIRY", "notes": "",
                "batch_id[]": [str(self.batch.pk)], "quantity[]": ["3"],
                "cost_price[]": [""],
            },
            "supplier": {"name": "New Traders", "phone": "0300", "address": "Lahore"},
            "supplier_payment": {"supplier_id": self.supplier.pk, "amount": "1000",
                                 "date": today, "method": "CASH", "notes": ""},
            "customer": {"type": "RETAIL", "name": "Walk-in Co", "shop_name": "",
                         "phone": "0300", "area": "", "credit_limit": "0",
                         "is_active": "on"},
            "customer_payment": {"customer_id": self.customer.pk, "amount": "500",
                                 "date": today, "method": "CASH", "notes": ""},
            "lab": {"patient": self.patient.pk, "tests": [str(self.lab_test.pk)]},
            "lab_result": {
                "order_id": self.order.pk, "status": "Completed",
                "results-TOTAL_FORMS": "1", "results-INITIAL_FORMS": "1",
                "results-MIN_NUM_FORMS": "0", "results-MAX_NUM_FORMS": "1000",
                "results-0-id": str(self.result_row.pk),
                "results-0-test_order": str(self.order.pk),
                "results-0-lab_test": str(self.lab_test.pk),
                "results-0-result_value": "12.4", "results-0-normal_range": "12-16",
                "results-0-unit": "g/dL", "results-0-remarks": "",
            },
            "lab_test": {"add": "1", "name": "LFT", "new_category": "Biochemistry",
                         "price": "900", "unit": "", "normal_range": ""},
            "imaging": {"patient": self.patient.pk, "modality": "ULTRASOUND",
                        "study_name": "Pelvis US", "clinical_note": "",
                        "price": "1500"},
            "imaging_report": {"study_id": self.study.pk, "status": "Reported",
                               "findings": "Normal study.", "impression": "Normal"},
            "scan_type": {"add": "1", "name": "Chest X-Ray", "modality": "XRAY",
                          "price": "800"},
            "ward": {"name": "Ward 2", "ward_type": "ICU", "daily_rate": "5000"},
            "bed": {"bed_number": "B-77", "ward": self.ward.pk, "status": "Available"},
            "admission": {
                "patient": self.other_patient.pk, "bed": self.free_bed.pk,
                "attending_doctor": self.doctor.pk,
                "admission_reason": "Dehydration",
            },
            "admission_advise": {"patient_id": self.patient.pk,
                                 "reason": "Needs IV fluids", "preferred_ward": ""},
            "round": {"admission_id": self.admission.pk, "vitals_temp": "98.6",
                      "vitals_bp": "120/80", "vitals_pulse": "78",
                      "clinical_notes": "Stable", "prescription_updates": ""},
            "medication": {
                "admission_id": self.admission.pk, "medicine": str(self.medicine.pk),
                "medicine_name": "Panadol", "dosage": "500mg", "quantity": "2",
                "source": "PHARMACY", "administered_at": now, "notes": "",
            },
            "vital": {
                "admission_id": self.admission.pk, "taken_at": now,
                "temperature": "99.0", "pulse": "82", "respiratory_rate": "18",
                "systolic_bp": "120", "diastolic_bp": "80", "spo2": "97",
                "consciousness": "A", "pain_score": "2", "blood_glucose": "",
                "notes": "resting",
            },
            "fluid": {
                "admission_id": self.admission.pk, "recorded_at": now,
                "direction": "IN", "kind": "IV fluid", "volume_ml": "500", "notes": "",
            },
            "nursing_note": {
                "admission_id": self.admission.pk, "noted_at": now,
                "shift": "MORNING", "note": "Patient comfortable, ate breakfast.",
            },
            "care_task": {
                "admission_id": self.admission.pk, "done_at": now,
                "task": "TURN", "notes": "left lateral",
            },
            "handover": {
                "admission_id": self.admission.pk,
                "date": now.split("T")[0], "shift": "MORNING",
                "situation": "Post-op day 1", "background": "", "assessment": "Stable",
                "recommendation": "Continue IV antibiotics",
            },
            "discharge": {"admission_id": self.admission.pk,
                          "discharge_notes": "Recovered, advised rest."},
            "surgery": {
                "patient": self.patient.pk, "admission": "",
                "procedure": self.procedure.pk, "start_time": now, "end_time": "",
                "lead_surgeon": self.doctor.pk, "surgical_team": "",
                "anesthesia_type": "General", "operation_notes": "Uneventful.",
                "outcome": "Successful",
            },
            "surgery_advise": {"patient_id": self.patient.pk,
                               "reason": "Acute appendicitis", "urgency": "Urgent",
                               "procedure": self.procedure.pk},
            "surgery_category": {"name": "Orthopaedics"},
            "procedure": {"name": "ORIF Femur", "category": self.surg_cat.pk,
                          "standard_charge": "80000"},
            "expense": {"date": today, "category": "UTILITIES",
                        "description": "Electricity", "amount": "12000",
                        "payment_method": "CASH"},
            "cash_closing": {"date": today, "opening": "1000", "counted": "1500",
                             "note": ""},
            # clinical add-ons
            "antenatal_visit": {"pregnancy_id": self.pregnancy.pk, "date": today,
                                "weight": "62", "bp": "120/80", "complaints": ""},
            "vaccination": {"patient": self.patient.pk, "vaccine": self.vaccine.pk,
                            "dose_number": "1", "date_given": today, "batch_no": "B1",
                            "given_by": "Nurse"},
            "diagnosis": {"patient": self.patient.pk, "code": self.dx_code.pk,
                          "diagnosed_on": today, "clinical_note": "URTI"},
            "referral": {"patient": self.patient.pk, "direction": "OUT",
                         "facility": "DHQ Hospital", "reason": "Specialist opinion",
                         "urgency": "ROUTINE", "status": "PENDING", "referral_date": today},
            "consent": {"patient": self.patient.pk, "consent_type": "SURGERY",
                        "title": "Surgery Consent", "body": "I consent to the procedure.",
                        "signed_by": "Patient", "signed_on": today},
            "birth_certificate": {"sex": "M", "date_of_birth": today,
                                  "mother_name": "Zainab Bibi", "child_name": "Baby Ali",
                                  "registered_on": today},
            "death_certificate": {"deceased_name": "Old Man", "sex": "M",
                                  "date_of_death": today, "cause_of_death": "Cardiac arrest",
                                  "registered_on": today},
        }


class EveryKindAppliesTest(AllKindsBase):
    def setUp(self):
        super().setUp()
        from patients.models import Patient
        # `admission` needs a patient who is not already occupying a bed.
        self.other_patient = Patient.objects.create(
            full_name="Bilal Khan", gender="M", age_years=45, hospital=self.h)

    def test_every_registered_kind_actually_applies(self):
        """Walks HANDLERS, so a new kind with no payload here fails the suite.

        `discharge` is exercised on its own below — it closes the admission that
        `round` and `medication` need, and ordering between them would make this
        test depend on dict order.
        """
        payloads = self.payloads()
        deferred = {"discharge"}

        missing = set(HANDLERS) - set(payloads)
        self.assertFalse(
            missing,
            f"These offline kinds have no test payload, so nobody knows whether "
            f"they work: {sorted(missing)}")

        for kind in HANDLERS:
            if kind in deferred:
                continue
            with self.subTest(kind=kind):
                self._apply(kind, payloads[kind], uuid=f"all-{kind}")

    def test_discharge_bills_the_stay_and_frees_the_bed(self):
        from billing.models import Invoice
        result = self._apply("discharge", self.payloads()["discharge"])
        self.admission.refresh_from_db()
        self.assertEqual(self.admission.status, "Discharged")
        self.admission.bed.refresh_from_db()
        self.assertEqual(self.admission.bed.status, "Available")
        self.assertTrue(Invoice.objects.filter(pk=result["invoice_id"]).exists())

    def test_a_replayed_discharge_never_bills_the_stay_twice(self):
        from billing.models import Invoice
        data = self.payloads()["discharge"]
        self._apply("discharge", data, uuid="dis-1")
        again = self._sync("discharge", data, "dis-2")   # a different device/tab
        self.assertEqual(again["status"], "failed")      # permanent, not retried
        self.assertEqual(Invoice.objects.count(), 1)


class TemplatesAndHandlersAgreeTest(TestCase):
    """A `data-offline-kind` with no handler queues entries that can never apply;
    a handler with no template is dead code nobody will notice is broken."""

    def _tagged_kinds(self):
        import re
        from pathlib import Path
        from django.conf import settings

        root = Path(settings.BASE_DIR) / "templates"
        found = set()
        for path in root.rglob("*.html"):
            found.update(re.findall(r'data-offline-kind="([a-z_]+)"',
                                    path.read_text(encoding="utf-8")))
        return found

    def test_every_tagged_form_has_a_handler(self):
        orphans = self._tagged_kinds() - set(HANDLERS)
        self.assertFalse(
            orphans,
            f"These forms are marked offline-capable but no handler can apply "
            f"them — anything typed into them offline is lost: {sorted(orphans)}")

    def test_every_handler_has_a_form_that_uses_it(self):
        unused = set(HANDLERS) - self._tagged_kinds()
        self.assertFalse(
            unused,
            f"These handlers exist but no template posts to them: {sorted(unused)}")


class OfflineResultsAreRealTest(AllKindsBase):
    """Spot-checks that the records offline replay creates are complete — not
    just that the endpoint answered 'applied'."""

    def setUp(self):
        super().setUp()
        from patients.models import Patient
        self.other_patient = Patient.objects.create(
            full_name="Bilal Khan", gender="M", age_years=45, hospital=self.h)

    def test_an_offline_prescription_keeps_its_medicines(self):
        from prescriptions.models import Prescription
        result = self._apply("prescription", self.payloads()["prescription"])
        rx = Prescription.objects.get(pk=result["prescription_id"])
        self.assertEqual(rx.appointment, self.appointment)
        self.assertEqual(rx.items.count(), 1)          # not an empty header
        self.assertEqual(rx.items.first().medicine, self.medicine)

    def test_an_offline_lab_order_raises_its_bill(self):
        from billing.models import Invoice
        result = self._apply("lab", self.payloads()["lab"])
        self.assertIsNotNone(result["invoice_id"])
        self.assertTrue(Invoice.objects.filter(pk=result["invoice_id"]).exists())

    def test_an_offline_scan_raises_its_bill(self):
        result = self._apply("imaging", self.payloads()["imaging"])
        self.assertIsNotNone(result["invoice_id"])

    def test_an_offline_appointment_raises_the_consultation_bill(self):
        from billing.models import Invoice
        result = self._apply("appointment", self.payloads()["appointment"])
        self.assertTrue(Invoice.objects.filter(
            appointment_id=result["appointment_id"]).exists())

    def test_an_offline_ward_dose_reduces_stock_and_will_be_billed(self):
        from ipd.models import MedicationLog
        result = self._apply("medication", self.payloads()["medication"])
        log = MedicationLog.objects.get(pk=result["log_id"])
        self.assertEqual(log.admission, self.admission)
        self.assertEqual(log.unit_price, self.medicine.price)   # frozen at give-time
        self.medicine.refresh_from_db()
        self.assertEqual(self.medicine.quantity, 98)

    def test_an_offline_surgery_raises_its_invoice(self):
        from billing.models import Invoice
        self._apply("surgery", self.payloads()["surgery"])
        self.assertTrue(Invoice.objects.filter(
            items__description__startswith="OT Surgery").exists())

    def test_an_offline_admission_takes_the_bed(self):
        result = self._apply("admission", self.payloads()["admission"])
        self.free_bed.refresh_from_db()
        self.assertEqual(self.free_bed.status, "Occupied")
        self.assertEqual(result["bed"], "B-2")

    def test_an_offline_admission_into_a_taken_bed_is_rejected_permanently(self):
        data = self.payloads()["admission"]
        self._apply("admission", data, uuid="adm-ok")
        # a second device queued the same bed while both were offline
        data2 = dict(data, patient=self.patient.pk)
        res = self._sync("admission", data2, "adm-clash")
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res["permanent"])


class MissingParentIsPermanentTest(AllKindsBase):
    """A queued entry whose parent id never made it into the form must be filed
    as rejected — the old behaviour retried it for ever behind a badge that said
    'waiting to sync'."""

    def setUp(self):
        super().setUp()
        from patients.models import Patient
        self.other_patient = Patient.objects.create(
            full_name="Bilal Khan", gender="M", hospital=self.h)

    def test_a_record_with_no_patient_id_is_rejected_not_retried(self):
        data = dict(self.payloads()["patient_record"])
        data.pop("patient_id")
        res = self._sync("patient_record", data, "no-parent")
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res["permanent"])
        self.assertEqual(
            ClientAction.objects.get(client_uuid="no-parent").status,
            ClientAction.FAILED)

    def test_a_parent_that_has_been_deleted_is_rejected_not_retried(self):
        data = dict(self.payloads()["round"], admission_id=999999)
        res = self._sync("round", data, "gone-parent")
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res["permanent"])


class LedgerIsAtomicTest(AllKindsBase):
    """The idempotency ledger must commit with the work it describes."""

    def setUp(self):
        super().setUp()
        from patients.models import Patient
        self.other_patient = Patient.objects.create(
            full_name="Bilal Khan", gender="M", hospital=self.h)

    def test_a_rejected_action_leaves_no_applied_ledger_row(self):
        bad = {"full_name": "", "gender": "M"}      # name is required
        self._sync("patient", bad, "rollback-1")
        row = ClientAction.objects.get(client_uuid="rollback-1")
        self.assertEqual(row.status, ClientAction.FAILED)
        self.assertEqual(row.result, {})

    def test_a_transient_failure_records_nothing_so_the_client_can_retry(self):
        """An unexpected server error must not leave a claim behind — otherwise
        the retry is answered from the ledger and the entry is silently dropped."""
        from unittest.mock import patch

        def boom(request, data):
            raise RuntimeError("database is locked")

        # Patch the registry entry, not the function object — the view looks the
        # handler up through HANDLERS.
        with patch.dict("offline_sync.handlers.HANDLERS", {"patient": boom}):
            res = self._sync("patient", self.payloads()["patient"], "boom-1")
        self.assertEqual(res["status"], "error")
        self.assertTrue(res["retry"])
        self.assertFalse(ClientAction.objects.filter(client_uuid="boom-1").exists())

        # ...and the retry, once the server is well again, applies normally.
        result = self._apply("patient", self.payloads()["patient"], uuid="boom-1")
        self.assertTrue(result["mrn"])

    def test_the_result_of_an_applied_action_is_stored_for_replays(self):
        first = self._apply("patient", self.payloads()["patient"], uuid="store-1")
        replay = self._sync("patient", self.payloads()["patient"], "store-1")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["result"]["mrn"], first["mrn"])
        from patients.models import Patient
        self.assertEqual(Patient.objects.filter(full_name="Queued Patient").count(), 1)


class SyncedQueueDoesNotRingLikeNewWorkTest(AllKindsBase):
    """A desk works through an outage; the queue syncs. Everything it raises is
    *history*, and must not arrive looking like a fresh pile of jobs — the doctor
    would be pinged about patients seen hours ago, and reception told to allot a
    bed that is already occupied."""

    def setUp(self):
        super().setUp()
        from accounts.models import Notification
        from patients.models import Patient
        self.other_patient = Patient.objects.create(
            full_name="Bilal Khan", gender="M", hospital=self.h)
        # Somebody must actually be on the receiving end of these.
        self.doc_user = User.objects.create_user(
            email="doc@sgh.com", password="pw", role="DOCTOR", hospital=self.h)
        self.doctor.user = self.doc_user
        self.doctor.save(update_fields=["user"])
        self.reception = User.objects.create_user(
            email="rec@sgh.com", password="pw", role="RECEPTIONIST", hospital=self.h)
        Notification.objects.all().delete()

    def _batch(self, actions):
        return self.client.post(
            reverse("offline_sync"),
            data=json.dumps({"actions": actions}),
            content_type="application/json")

    def test_ten_queued_visits_do_not_ring_the_doctor_ten_times(self):
        from accounts.models import Notification

        base = self.payloads()["visit"]
        actions = []
        for i in range(10):
            data = dict(base, full_name=f"Walk In {i}", phone=f"030011122{i:02d}")
            actions.append({"uuid": f"storm-{i}", "kind": "visit", "data": data,
                            "at": "2026-08-03T10:32:00+05:00"})
        self._batch(actions)

        doc_notes = Notification.objects.filter(user=self.doc_user)
        self.assertEqual(doc_notes.count(), 11)      # 10 kept as history + 1 summary
        unread = doc_notes.filter(is_read=False)
        self.assertEqual(unread.count(), 1, "the bell must ring once, not ten times")
        self.assertIn("10 entries made offline", unread.first().message)

    def test_the_history_says_when_it_actually_happened(self):
        """'New Patient assigned ... is in your queue' is a lie four hours later."""
        from accounts.models import Notification
        self._batch([{"uuid": "when-1", "kind": "visit",
                      "data": self.payloads()["visit"],
                      "at": "2026-08-03T10:32:00+05:00"}])
        note = Notification.objects.filter(user=self.doc_user, is_read=True).first()
        self.assertIsNotNone(note)
        self.assertTrue(note.message.startswith("⏱"), note.message)
        self.assertIn("offline", note.message)

    def test_work_that_is_still_outstanding_still_rings(self):
        """A short-stock sale needs the shelf counted whenever it was rung up, so
        that one must NOT be filed away as history."""
        from accounts.models import Notification
        pharmacist = User.objects.create_user(
            email="ph@sgh.com", password="pw", role="PHARMACIST", hospital=self.h)
        # Stock is the batches, not the aggregate — shorten both or nothing is short.
        self.medicine.quantity = 1
        self.medicine.save(update_fields=["quantity"])
        self.batch.quantity = 1
        self.batch.save(update_fields=["quantity"])

        data = dict(self.payloads()["sale"])
        data["quantity[]"] = ["9"]
        data["paid"] = "180"          # 9 × 20, paid in full — a walk-in, no credit
        resp = self._batch([{"uuid": "short-1", "kind": "sale", "data": data,
                             "at": "2026-08-03T11:00:00+05:00"}])
        outcome = resp.json()["results"][0]
        self.assertEqual(outcome["status"], "applied", outcome.get("error"))
        self.assertTrue(outcome["result"]["needs_reconcile"], outcome["result"])

        note = Notification.objects.filter(user=pharmacist, is_read=False).first()
        self.assertIsNotNone(note, "the pharmacist must still be told to reconcile")
        self.assertIn("reconcile", note.message)

    def test_a_live_action_still_notifies_normally(self):
        """The replay window must not leak into ordinary online use — book a visit
        at the desk and the doctor is pinged, unread, with no history stamp."""
        from accounts.models import Notification
        self.client.post(reverse("visit_create"), data=self.payloads()["visit"])
        note = Notification.objects.filter(user=self.doc_user, is_read=False).first()
        self.assertIsNotNone(note)
        self.assertFalse(note.message.startswith("⏱"))

    def test_no_summary_when_a_batch_raised_nothing(self):
        from accounts.models import Notification
        self._batch([{"uuid": "quiet-1", "kind": "patient",
                      "data": self.payloads()["patient"], "at": None}])
        self.assertFalse(Notification.objects.filter(
            message__contains="made offline").exists())


class PingTest(TestCase):
    """The reachability probe the client uses instead of `navigator.onLine`."""

    def setUp(self):
        self.h = Hospital.objects.create(
            name="Shaheen", slug="sh", expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(
            email="p@p.com", password="pw", role="RECEPTIONIST", hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def test_a_signed_in_browser_gets_204(self):
        c = Client(); c.force_login(self.user)
        self.assertEqual(c.get(reverse("offline_ping")).status_code, 204)

    def test_an_expired_session_does_not_look_reachable(self):
        """It must not answer 2xx to a logged-out browser: the client would then
        submit natively and the entry would land on a login page."""
        resp = Client().get(reverse("offline_ping"))
        self.assertNotEqual(resp.status_code, 204)

    def test_the_queue_screen_opens(self):
        c = Client(); c.force_login(self.user)
        self.assertEqual(c.get(reverse("offline_queue")).status_code, 200)


class ProvisionalSlipTest(TestCase):
    """With no server between two devices no notification can travel, so the paper
    slip IS the handoff: reception prints it offline and the patient carries it to
    the doctor. If this page will not open or render offline, the offline flow stops
    at the front desk."""

    def setUp(self):
        self.h = Hospital.objects.create(
            name="Shaheen", slug="sh", expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(
            email="r@r.com", password="pw", role="RECEPTIONIST", hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def test_the_slip_opens_and_carries_the_letterhead(self):
        resp = self.client.get(reverse("offline_slip"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shaheen")          # tenant letterhead, not a blank page

    def test_it_reads_the_outbox_rather_than_the_database(self):
        """Nothing on it may come from the server — the entry has not synced yet."""
        body = self.client.get(reverse("offline_slip")).content.decode()
        self.assertIn("PharmaOffline", body)
        self.assertIn("js/offline.js", body)          # print base does not include it

    def test_it_says_the_number_is_not_the_real_one(self):
        """A provisional reference printed as if it were the token is how two
        patients end up holding the same number."""
        body = self.client.get(reverse("offline_slip")).content.decode()
        self.assertIn("Provisional", body)
        self.assertIn("will be issued when it syncs", body)

    def test_it_is_pre_cached_so_it_opens_with_no_connection(self):
        from user_mgmt.pwa_views import _shell_urls
        self.assertIn(reverse("offline_slip"), _shell_urls())
