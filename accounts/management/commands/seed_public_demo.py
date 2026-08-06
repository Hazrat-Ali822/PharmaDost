"""Seed a self-contained public DEMO tenant so anyone can try the whole system.

Creates an isolated "Sehatyar Demo Hospital", one demo user per role (all with
password ``demo1122``), and realistic sample data in EVERY module — pharmacy,
OPD, prescriptions, lab, imaging, IPD + nursing, OT, billing. Everything is
stamped to the demo hospital, so it never mixes with a real tenant's data.

The /demo/ route (accounts.views.demo_login) signs a visitor straight in as
``demo@sehatyar.online``.

    python manage.py seed_public_demo            # create once (safe to re-run)
    python manage.py seed_public_demo --reset    # wipe the demo tenant and rebuild

Re-running without --reset only refreshes the users/passwords and leaves existing
demo data untouched — it never duplicates or deletes.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


DEMO_SLUG = "demo"
DEMO_PASSWORD = "demo1122"
DEMO_BRAND = "Sehatyar Demo Hospital"

# email -> (role, full name).  The first is the admin the /demo/ link logs into.
DEMO_USERS = [
    ("demo@sehatyar.online", "ADMIN", "Demo Admin"),
    ("demo.reception@sehatyar.online", "RECEPTIONIST", "Sana Reception"),
    ("demo.doctor@sehatyar.online", "DOCTOR", "Dr. Imran Khan"),
    ("demo.nurse@sehatyar.online", "NURSE", "Nurse Ayesha"),
    ("demo.pharmacist@sehatyar.online", "PHARMACIST", "Bilal Pharmacist"),
    ("demo.labtech@sehatyar.online", "LABTECH", "Kamran Lab"),
    ("demo.radiology@sehatyar.online", "SONOGRAPHER", "Hina Radiology"),
    ("demo.accounts@sehatyar.online", "ACCOUNTANT", "Nadeem Accounts"),
    ("demo.wholesale@sehatyar.online", "WHOLESALE", "Kashif Wholesale"),
]
DEMO_EMAILS = [e for e, *_ in DEMO_USERS]

MEDS = [
    # name, generic, brand, mfr, category, retail, wholesale, pack, reorder
    ("Panadol", "Paracetamol", "Panadol", "GSK", "TABLET", 30, 25, "10x10", 20),
    ("Augmentin 625", "Amoxicillin+Clav", "Augmentin", "GSK", "TABLET", 250, 230, "1x6", 10),
    ("Brufen 400", "Ibuprofen", "Brufen", "Abbott", "TABLET", 40, 34, "10x10", 20),
    ("Calpol Syrup", "Paracetamol", "Calpol", "GSK", "SYRUP", 90, 80, "90ml", 8),
    ("Ventolin Inhaler", "Salbutamol", "Ventolin", "GSK", "INHALER", 450, 420, "1 unit", 5),
    ("Risek 20", "Omeprazole", "Risek", "Getz", "CAPSULE", 220, 200, "2x14", 10),
    ("Amoxil 500", "Amoxicillin", "Amoxil", "GSK", "CAPSULE", 180, 165, "10x10", 12),
    ("Flagyl 400", "Metronidazole", "Flagyl", "Sanofi", "TABLET", 60, 54, "10x10", 10),
    ("Insulin Novomix", "Insulin", "Novomix", "NovoNordisk", "INJECTION", 1300, 1250, "1 pen", 4),
    ("ORS Sachet", "ORS", "ORS", "Searle", "SACHET", 20, 17, "1 sachet", 30),
]

SUPPLIERS = [
    ("Demo Muller & Phipps", "0300-1112233", "Karachi"),
    ("Demo United Distributors", "0301-4455667", "Lahore"),
]

PATIENTS = [
    ("Ahmed Ali", "M", "0311-1111111", 34, "B+", ""),
    ("Fatima Bibi", "F", "0311-2222222", 28, "O+", "Penicillin"),
    ("Usman Tariq", "M", "0311-3333333", 45, "A+", ""),
    ("Zainab Noor", "F", "0311-4444444", 6, "AB+", "Sulpha drugs"),
    ("Hassan Raza", "M", "0311-5555555", 52, "B-", ""),
    ("Maryam Iqbal", "F", "0311-6666666", 19, "O-", ""),
    ("Bilal Ahmed", "M", "0311-7777777", 60, "A-", "Aspirin"),
    ("Sana Khan", "F", "0311-8888888", 31, "O+", ""),
]

LAB_CATALOG = [
    ("Haematology", [("CBC (Complete Blood Count)", 600, "", ""),
                     ("Hb (Haemoglobin)", 200, "g/dL", "12-16")]),
    ("Biochemistry", [("Blood Sugar Random", 250, "mg/dL", "70-140"),
                      ("LFT (Liver Function Test)", 1200, "", ""),
                      ("Serum Creatinine", 400, "mg/dL", "0.6-1.3")]),
]


class Command(BaseCommand):
    help = "Seed an isolated public demo tenant with data in every module."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete the existing demo tenant and rebuild it.")

    def handle(self, *args, **opts):
        from saas.models import Hospital
        from saas.utils import set_current_hospital, clear_current_hospital
        from patients.models import Patient

        rnd = random.Random(7)
        demo = Hospital.objects.filter(slug=DEMO_SLUG).first()

        if demo and opts["reset"]:
            self._reset(demo)
            demo = None

        already_seeded = bool(demo and Patient.objects.filter(hospital=demo).exists())
        if already_seeded:
            self._users(demo)  # refresh passwords/roles
            # Top up modules added after the demo was first seeded (panels,
            # emergency, HR, maternity, diagnosis, referral, certificates, blood
            # bank, vaccination, consent). Idempotent — a no-op once present.
            added = False
            set_current_hospital(demo)
            try:
                with transaction.atomic():
                    added = self._seed_addons(demo)
            finally:
                clear_current_hospital()
            self.stdout.write(self.style.WARNING(
                "Demo already seeded — refreshed logins" +
                (" and added the newer modules' data." if added else " only. Use --reset to rebuild.")))
            self._print_logins()
            return

        try:
            with transaction.atomic():
                if not demo:
                    demo = Hospital.objects.create(
                        name=DEMO_BRAND, slug=DEMO_SLUG,
                        expiry_date=timezone.localdate() + timedelta(days=3650))
                set_current_hospital(demo)
                self._branding(demo)
                users = self._users(demo)
                suppliers = self._suppliers()
                meds = self._medicines(suppliers, users["ADMIN"])
                customers = self._customers()
                patients = self._patients()
                depts, docs = self._doctors(users, depts_needed=True)
                self._appointments_rx(patients, docs, users, meds, rnd)
                self._sales(meds, customers, users, patients, rnd)
                self._lab(patients, users)
                self._imaging(patients, users)
                self._ipd(patients, docs, users, meds, rnd)
                self._ot(patients, docs)
                self._finance(users, rnd)
                self._seed_addons(demo)   # panels, emergency, HR, maternity, etc.
        finally:
            clear_current_hospital()

        self.stdout.write(self.style.SUCCESS(
            "\nDemo tenant seeded: '%s'." % DEMO_BRAND))
        self._print_logins()

    # -------------------------------------------------------------- helpers
    def _print_logins(self):
        self.stdout.write("Visitors: sehatyar.online/demo/  (one-click, no password)")
        self.stdout.write("All demo logins use password: '%s'" % DEMO_PASSWORD)
        for email, role, _ in DEMO_USERS:
            self.stdout.write(f"  {role:12} {email}")

    def _reset(self, demo):
        """Best-effort wipe of the demo tenant (only on --reset).

        `purge_tenant` clears every hospital-scoped row in dependency-safe passes
        (a plain `demo.delete()` can't — child rows hold PROTECT FKs to Medicine,
        Patient, Customer, …). Then the now-childless hospital, the demo doctors
        (no hospital FK) and the demo user accounts are removed."""
        from accounts.models import User
        from opd.models import Doctor, DoctorPayout
        from saas.services import purge_tenant
        with transaction.atomic():
            DoctorPayout.objects.filter(doctor__pmdc_no__startswith="DEMO-").delete()
            purge_tenant(demo)
            demo.delete()
            Doctor.objects.filter(pmdc_no__startswith="DEMO-").delete()
            User.objects.filter(email__in=DEMO_EMAILS).delete()

    def _branding(self, demo):
        from user_mgmt.models import SiteSettings
        s = SiteSettings.load()
        s.brand_name = "Sehatyar Demo"
        s.brand_tagline = "Live Demo — sample data"
        s.enabled_modules = None            # all modules on
        s.save()

    def _users(self, demo):
        from accounts.models import User
        out = {}
        for email, role, name in DEMO_USERS:
            u = User.objects.filter(email=email).first()
            if not u:
                u = User.objects.create_user(email=email, password=DEMO_PASSWORD)
            else:
                u.set_password(DEMO_PASSWORD)
            parts = name.split()
            u.role = role
            u.first_name = parts[0]
            u.last_name = " ".join(parts[1:])
            u.hospital = demo
            u.is_superuser = u.is_staff = False   # demo is never a superuser
            u.save()
            out[role] = u
        return out

    def _suppliers(self):
        from suppliers.models import Supplier
        return [Supplier.objects.create(name=n, phone=p, address=a, balance=Decimal("0"))
                for n, p, a in SUPPLIERS]

    def _medicines(self, suppliers, admin):
        from inventory.models import Medicine
        today = timezone.localdate()
        meds = []
        for i, (name, gen, brand, mfr, cat, retail, ws, pack, reorder) in enumerate(MEDS):
            m = Medicine.all_objects.create(
                name=name, generic_name=gen, brand=brand, manufacturer=mfr,
                category=cat, barcode=f"DEMO-{i+1:04d}", pack_size=pack,
                price=Decimal(retail), wholesale_price=Decimal(ws),
                reorder_level=reorder, quantity=0,
                expiry_date=today + timedelta(days=365))
            # a healthy batch, plus a near-expiry / low one here and there
            qty = [300, 60, 180, 40, 4, 120, 90, 50, 3, 200][i]
            exp = today + timedelta(days=(20 if i in (3, 8) else 400))
            cost = (m.price * Decimal("0.6")).quantize(Decimal("0.01"))
            m.add_stock(qty, batch_number=f"DB{i+1}", expiry_date=exp,
                        cost_price=cost, supplier=suppliers[i % len(suppliers)])
            meds.append(m)
        return meds

    def _customers(self):
        from customers.models import Customer
        rows = [
            ("RETAIL", "Walk-in Khata", "", "0921000001", "Model Town", 50000),
            ("WHOLESALE", "Demo City Medical Store", "Demo City Medical Store",
             "0922000001", "Saddar", 200000),
        ]
        return [Customer.objects.create(type=t, name=n, shop_name=s, phone=p,
                                        area=a, credit_limit=Decimal(l), balance=Decimal("0"))
                for t, n, s, p, a, l in rows]

    def _patients(self):
        from patients.models import Patient
        out = []
        for name, gender, phone, age, bg, allergy in PATIENTS:
            out.append(Patient.objects.create(
                full_name=name, gender=gender, phone=phone, age_years=age,
                blood_group=bg, allergies=allergy))
        return out

    def _doctors(self, users, depts_needed=True):
        from opd.models import Department, Doctor, DoctorSchedule
        depts = {}
        for name in ["Medicine", "Gynaecology", "Paediatrics", "Surgery"]:
            depts[name] = Department.objects.create(name=name)
        doc_user = users["DOCTOR"]
        d1 = Doctor.objects.create(
            user=doc_user, full_name="Dr. Imran Khan", department=depts["Medicine"],
            specialty="General Physician", pmdc_no="DEMO-1001",
            opd_fee=Decimal("1000"), followup_fee=Decimal("500"), share_percent=Decimal("70"))
        d2 = Doctor.objects.create(
            full_name="Dr. Sara Ahmed", department=depts["Gynaecology"],
            specialty="Gynaecologist", pmdc_no="DEMO-1002",
            opd_fee=Decimal("1500"), followup_fee=Decimal("700"), share_percent=Decimal("60"))
        d3 = Doctor.objects.create(
            full_name="Dr. Kamal Riaz", department=depts["Surgery"],
            specialty="Surgeon", pmdc_no="DEMO-1003",
            opd_fee=Decimal("2000"), followup_fee=Decimal("1000"), share_percent=Decimal("65"))
        # weekly OPD timings so the availability board isn't empty
        from datetime import time
        for wd in range(0, 5):
            DoctorSchedule.objects.create(doctor=d1, weekday=wd,
                                          start_time=time(9, 0), end_time=time(14, 0))
        return depts, [d1, d2, d3]

    def _appointments_rx(self, patients, docs, users, meds, rnd):
        from opd.models import Appointment, ClinicalRecord
        from prescriptions.models import Prescription, PrescriptionItem, RxPreset, RxPresetItem
        from billing.services import create_service_invoice
        today = timezone.localdate()
        doc_user = users["DOCTOR"]

        for i, p in enumerate(patients):
            doc = docs[i % len(docs)]
            appt = Appointment.objects.create(
                patient=p, doctor=doc,
                appointment_date=today - timedelta(days=rnd.randint(0, 4)),
                visit_type="OPD", status=rnd.choice(["BOOKED", "DONE", "ARRIVED"]))
            create_service_invoice(
                patient=p, appointment=appt, created_by=doc_user,
                items=[(f"OPD Consultation — {doc.full_name}", doc.opd_fee)],
                paid=(doc.opd_fee if i % 2 == 0 else Decimal("0.00")))
            # a prescription on the first few so the pharmacy queue isn't empty
            if i < 4:
                rx = Prescription.objects.create(
                    appointment=appt, complaint="Fever, cough",
                    diagnosis="Viral infection", status="PENDING")
                for m in rnd.sample(meds, 2):
                    PrescriptionItem.objects.create(
                        prescription=rx, medicine=m, dosage="1+0+1",
                        duration_days=5, instructions="After meals")

        ClinicalRecord.objects.create(
            patient=patients[0], doctor=docs[0], record_type="CONSULT",
            title="Fever & body aches", diagnosis="Viral fever",
            details="Advised rest, fluids, paracetamol.", bp="118/76",
            pulse="88", temperature="101 F", weight="72 kg", created_by=doc_user)

        # a reusable prescription template (hospital is a hard NOT NULL here, so set
        # it explicitly rather than leaning on the auto-stamp signal)
        from saas.utils import get_current_hospital
        preset = RxPreset.objects.create(hospital=get_current_hospital(),
                                         name="Flu / Cold pack",
                                         description="Common flu combination")
        for m in meds[:2]:
            RxPresetItem.objects.create(preset=preset, medicine=m, dosage="1+1+1",
                                        duration_days=3, instructions="After meals")

    def _sales(self, meds, customers, users, patients, rnd):
        from sales.services import create_sale
        from sales.models import Sale
        pharmacist = users["PHARMACIST"]
        wholesaler = users["WHOLESALE"]
        retail = [c for c in customers if c.type == "RETAIL"]
        wholesale = [c for c in customers if c.type == "WHOLESALE"]
        sellable = [m for m in meds if not m.is_expired and m.quantity > 5]

        for n in range(14):
            picks = rnd.sample(sellable, rnd.randint(1, 3))
            items = [{"medicine_id": m.id, "quantity": rnd.randint(1, 3)} for m in picks]
            try:
                if n % 4 == 0 and wholesale:
                    guess = sum(m.wholesale_price * 3 for m in picks)
                    sale = create_sale(items=items, sale_type=Sale.WHOLESALE,
                                       customer=wholesale[0],
                                       paid=(guess * Decimal("0.5")).quantize(Decimal("0.01")),
                                       payment_method="CREDIT", cashier=wholesaler)
                elif n % 4 == 1 and retail:
                    sale = create_sale(items=items, sale_type=Sale.RETAIL,
                                       customer=retail[0], paid=Decimal("0.00"),
                                       payment_method="CREDIT", cashier=pharmacist)
                else:
                    pat = rnd.choice(patients) if rnd.random() < 0.5 else None
                    sale = create_sale(items=items, sale_type=Sale.RETAIL,
                                       customer_name="Walk-in", payment_method="CASH",
                                       cashier=pharmacist, patient=pat)
            except ValueError:
                continue
            sale.created_at = timezone.now() - timedelta(days=rnd.randint(0, 18))
            sale.save(update_fields=["created_at"])

    def _lab(self, patients, users):
        from lab.models import TestCategory, LabTest, TestOrder, TestResult
        from billing.services import create_service_invoice
        cats = {}
        tests = []
        for cat_name, rows in LAB_CATALOG:
            cat, _ = TestCategory.objects.get_or_create(name=cat_name)
            cats[cat_name] = cat
            for tname, price, unit, rng in rows:
                t, _ = LabTest.objects.get_or_create(
                    category=cat, name=tname,
                    defaults=dict(price=Decimal(price), unit=unit, normal_range=rng))
                tests.append(t)
        labtech = users["LABTECH"]
        # a completed order (with results) and a pending one
        o1 = TestOrder.objects.create(patient=patients[0], ordered_by=labtech,
                                      status="Completed")
        for t in tests[:2]:
            TestResult.objects.create(test_order=o1, lab_test=t, result_value="Normal")
        create_service_invoice(patient=patients[0], created_by=labtech,
                               items=[(t.name, t.price) for t in tests[:2]])
        o2 = TestOrder.objects.create(patient=patients[4], ordered_by=labtech,
                                      status="Pending")
        for t in tests[2:4]:
            TestResult.objects.create(test_order=o2, lab_test=t)

    def _imaging(self, patients, users):
        from imaging.models import ImagingStudy
        from billing.services import create_service_invoice
        sono = users["SONOGRAPHER"]
        us = ImagingStudy.objects.create(
            patient=patients[2], modality="ULTRASOUND",
            study_name="Abdominal Ultrasound (Complete)",
            clinical_note="RUQ pain, r/o gallstones.", performed_by=sono,
            status="Reported", price=Decimal("1200"),
            findings="Liver mildly enlarged. Gallbladder normal, no calculi.",
            impression="Grade I fatty liver.")
        create_service_invoice(patient=us.patient, created_by=sono,
                               items=[(us.study_name, us.price)])
        ImagingStudy.objects.create(
            patient=patients[3], modality="XRAY", study_name="Chest X-Ray (PA)",
            clinical_note="Cough x 4 days.", performed_by=sono, status="Pending",
            price=Decimal("800"))

    def _ipd(self, patients, docs, users, meds, rnd):
        from ipd.models import (Ward, Bed, Admission, DoctorRound, MedicationLog,
                                VitalsObservation, FluidBalanceEntry, NursingNote,
                                CareTask, ShiftHandover, NurseShift, PatientAllocation)
        nurse = users["NURSE"]
        admin = users["ADMIN"]
        today = timezone.localdate()
        now = timezone.now()

        w1 = Ward.objects.create(name="General Ward", ward_type="General Male",
                                 daily_rate=Decimal("2000"), in_charge=nurse)
        w2 = Ward.objects.create(name="Private Rooms", ward_type="Private",
                                 daily_rate=Decimal("5000"))
        beds = [Bed.objects.create(bed_number=f"G-{i}", ward=w1) for i in range(1, 5)]
        beds += [Bed.objects.create(bed_number=f"P-{i}", ward=w2) for i in range(1, 3)]

        # admit two patients
        admissions = []
        for k, (p, doc) in enumerate([(patients[4], docs[0]), (patients[6], docs[2])]):
            bed = beds[k]
            adm = Admission.objects.create(
                patient=p, bed=bed, attending_doctor=doc,
                admission_reason="Acute gastroenteritis with dehydration"
                if k == 0 else "Post-operative observation",
                admission_date=now - timedelta(days=2 - k), status="Admitted")
            bed.status = "Occupied"; bed.save(update_fields=["status"])
            admissions.append(adm)

            DoctorRound.objects.create(
                admission=adm, clinical_notes="Patient stable, continuing IV fluids.",
                vitals_temp="99 F", vitals_bp="120/80", vitals_pulse="82")
            MedicationLog.objects.create(
                admission=adm, medicine=meds[0], medicine_name=meds[0].name,
                dosage="1 tab", quantity=1, source="PATIENT",
                administered_by=nurse, notes="Given with water")
            VitalsObservation.objects.create(
                admission=adm, taken_by=nurse, temperature=Decimal("100.4"),
                pulse=92, respiratory_rate=18, systolic_bp=118, diastolic_bp=76,
                spo2=97, consciousness="A", pain_score=2)
            FluidBalanceEntry.objects.create(admission=adm, recorded_by=nurse,
                                             direction="IN", kind="IV Normal Saline",
                                             volume_ml=500)
            FluidBalanceEntry.objects.create(admission=adm, recorded_by=nurse,
                                             direction="OUT", kind="Urine", volume_ml=350)
            NursingNote.objects.create(admission=adm, noted_by=nurse, shift="MORNING",
                                       note="Patient comfortable, tolerating oral fluids.")
            CareTask.objects.create(admission=adm, task="HYGIENE", done_by=nurse,
                                    notes="Morning sponge bath done.")
            ShiftHandover.objects.create(
                admission=adm, shift="MORNING", from_nurse=nurse,
                situation="Stable, on IV fluids.",
                background="Admitted with dehydration.",
                assessment="Improving, vitals stable.",
                recommendation="Continue fluids, monitor output.")

        # roster + allocation for the current week so those screens aren't empty
        for d in range(0, 3):
            NurseShift.objects.get_or_create(
                nurse=nurse, ward=w1, date=today + timedelta(days=d),
                shift="MORNING", defaults=dict(duty="STAFF", created_by=admin))
        for adm in admissions:
            PatientAllocation.objects.get_or_create(
                admission=adm, date=today, shift="MORNING",
                defaults=dict(nurse=nurse, assigned_by=admin))

    def _ot(self, patients, docs):
        from ot.models import SurgeryCategory, SurgeryProcedure, SurgeryRecord
        cat = SurgeryCategory.objects.create(name="General Surgery")
        proc = SurgeryProcedure.objects.create(
            name="Appendectomy", category=cat, standard_charge=Decimal("45000"))
        SurgeryProcedure.objects.create(
            name="Hernia Repair", category=cat, standard_charge=Decimal("60000"))
        SurgeryRecord.objects.create(
            patient=patients[6], procedure=proc,
            start_time=timezone.now() - timedelta(days=1, hours=2),
            end_time=timezone.now() - timedelta(days=1),
            lead_surgeon=docs[2], anesthesia_type="General",
            operation_notes="Uncomplicated laparoscopic appendectomy.",
            outcome="Successful")

    def _finance(self, users, rnd):
        from billing.models import Expense, CashClosing
        from billing.services import cash_position
        from opd.models import DoctorPayout, Doctor
        admin = users["ADMIN"]
        today = timezone.localdate()
        for cat, desc, amt, days in [
                ("RENT", "Monthly building rent", Decimal("60000"), 2),
                ("SALARY", "Staff salaries", Decimal("120000"), 1),
                ("UTILITIES", "Electricity bill", Decimal("18000"), 3)]:
            Expense.objects.create(date=today - timedelta(days=days), category=cat,
                                   description=desc, amount=amt, payment_method="CASH",
                                   recorded_by=admin)
        yday = today - timedelta(days=1)
        if not CashClosing.objects.filter(date=yday).exists():
            pos = cash_position(yday)
            opening = Decimal("5000")
            expected = opening + pos["net"]
            CashClosing.objects.create(
                date=yday, opening=opening, cash_in=pos["cash_in"],
                cash_out=pos["cash_out"], expected=expected, counted=expected,
                difference=Decimal("0.00"), note="Day-end", closed_by=admin)
        d1 = Doctor.objects.filter(pmdc_no="DEMO-1001").first()
        if d1:
            DoctorPayout.objects.create(doctor=d1, date=yday, amount=Decimal("1000"),
                                        payment_method="CASH", note="Weekly settlement",
                                        paid_by=admin)

    # ----------------------------------------------------------- newer modules
    def _seed_addons(self, demo):
        """Seed the modules added after the original demo (panels, emergency, HR,
        maternity, diagnosis, referral, certificates, blood bank, vaccination,
        consent). Idempotent — returns False and does nothing if already present,
        so a plain re-run tops an old demo up exactly once. Runs under
        set_current_hospital(demo), so rows auto-stamp to the demo tenant."""
        from panels.models import Panel
        if Panel.objects.filter(hospital=demo).exists():
            return False
        from accounts.models import User
        from opd.models import Doctor
        from patients.models import Patient
        patients = list(Patient.objects.filter(hospital=demo).order_by("id"))
        docs = list(Doctor.objects.filter(pmdc_no__startswith="DEMO-").order_by("pmdc_no"))
        users = {u.role: u for u in User.objects.filter(email__in=DEMO_EMAILS)}
        if not (patients and docs and users):
            return False
        self._panels(patients, users)
        self._diagnosis(patients, docs, users)
        self._emergency(patients, docs, users)
        self._maternity(patients, docs, users)
        self._hr(users)
        self._referral(patients, docs, users)
        self._certificates(patients, docs, users)
        self._bloodbank(patients, users)
        self._vaccination(patients, users)
        self._consent(patients, docs, users)
        return True

    def _panels(self, patients, users):
        """Four cards with deliberately different covered-service mixes, so the
        demo shows the whole range: one covers everything, one is an inpatient
        Sehat Card that does NOT cover OPD/pharmacy, one is an outpatient panel,
        one covers OPD only. Each linked patient then gets both a covered bill
        (becomes a panel claim) and, where relevant, an uncovered bill (falls to
        the patient) — so the behaviour is visible on the ledger, not just told."""
        from panels.models import Panel, PanelPayment
        from billing.services import create_service_invoice
        acct = users["ACCOUNTANT"]

        p_full = Panel.objects.create(
            name="State Life Insurance", type=Panel.INSURANCE,
            contact_person="Claims Desk", phone="042-111-111-111",
            copay_percent=Decimal("10"), covered_services=[],      # empty = everything
            notes="Full corporate cover — all services")
        p_sehat = Panel.objects.create(
            name="Sehat Sahulat (Sehat Card)", type=Panel.SEHAT_CARD,
            contact_person="Sehat Card Facilitation", phone="0800-09009",
            covered_services=[Panel.SVC_IPD, Panel.SVC_LAB,
                              Panel.SVC_IMAGING, Panel.SVC_PROCEDURE],
            notes="Govt inpatient package — no OPD/pharmacy; annual family limit")
        p_opd = Panel.objects.create(
            name="Al-Khidmat OPD Panel", type=Panel.CORPORATE,
            contact_person="HR Desk", phone="042-222-222-222",
            covered_services=[Panel.SVC_OPD, Panel.SVC_PHARMACY, Panel.SVC_LAB],
            notes="Outpatient panel — OPD, pharmacy & lab only")
        p_only = Panel.objects.create(
            name="NRSP Microhealth", type=Panel.INSURANCE,
            contact_person="Member Services", phone="051-333-333-333",
            covered_services=[Panel.SVC_OPD],
            notes="Consultation-only cover — OPD free, everything else patient-paid")

        f, h, m, b = patients[1], patients[4], patients[5], patients[6]
        for pt, panel, card, limit in (
            (f, p_full, "SL-88231", Decimal("0")),
            (h, p_sehat, "SEHAT-3300-1122", Decimal("120000")),
            (m, p_opd, "AK-4521", Decimal("0")),
            (b, p_only, "NRSP-7788", Decimal("0")),
        ):
            pt.panel = panel
            pt.panel_member_id = card
            pt.panel_coverage_limit = limit
            pt.save(update_fields=["panel", "panel_member_id", "panel_coverage_limit"])

        # Covers everything → both bills become claims.
        create_service_invoice(patient=f, created_by=acct, service="OPD",
                               items=[("OPD Consultation", Decimal("1500"))])
        create_service_invoice(patient=f, created_by=acct, service="LAB",
                               items=[("Lab: LFT + CBC", Decimal("1800"))])
        # Sehat Card: LAB/IPD covered (claims) but OPD/pharmacy are NOT — that OPD
        # bill is paid by the patient, proving the per-service gate.
        create_service_invoice(patient=h, created_by=acct, service="IPD",
                               items=[("Admission + bed (2 days)", Decimal("9000"))])
        create_service_invoice(patient=h, created_by=acct, service="LAB",
                               items=[("Lab: Dengue NS1 + CBC", Decimal("1400"))])
        create_service_invoice(patient=h, created_by=acct, service="OPD",
                               items=[("OPD Consultation (not covered)", Decimal("1000"))])
        # OPD panel: consultation + lab covered; an imaging bill is not.
        create_service_invoice(patient=m, created_by=acct, service="OPD",
                               items=[("OPD Consultation", Decimal("1200"))])
        create_service_invoice(patient=m, created_by=acct, service="IMAGING",
                               items=[("Ultrasound (not covered)", Decimal("2000"))])
        # OPD-only card: consultation covered, X-ray not.
        create_service_invoice(patient=b, created_by=acct, service="OPD",
                               items=[("OPD Consultation", Decimal("800"))])
        create_service_invoice(patient=b, created_by=acct, service="IMAGING",
                               items=[("Chest X-ray (not covered)", Decimal("900"))])

        PanelPayment.objects.create(panel=p_full, amount=Decimal("1350"), method="BANK",
                                    reference="TRX-556677", received_by=acct,
                                    notes="Partial settlement")

    def _diagnosis(self, patients, docs, users):
        from django.core.management import call_command
        from diagnosis.models import DiagnosisCode, PatientDiagnosis
        call_command("seed_icd")
        doc_user = users["DOCTOR"]
        for code_str, pat, note in [
                ("I10", patients[4], "Essential hypertension, on treatment"),
                ("E11.9", patients[6], "Type 2 diabetes, diet-controlled"),
                ("J18.9", patients[3], "Community-acquired pneumonia")]:
            code = DiagnosisCode.objects.filter(code=code_str).first()
            if code:
                PatientDiagnosis.objects.create(patient=pat, code=code, doctor=docs[0],
                                                clinical_note=note, created_by=doc_user)

    def _emergency(self, patients, docs, users):
        from emergency.models import EmergencyCase
        doc = docs[0]
        recep = users["RECEPTIONIST"]
        EmergencyCase.objects.create(
            patient=patients[4], triage="RED", chief_complaint="Severe chest pain, sweating",
            mode_of_arrival="AMBULANCE", bp="90/60", pulse="120", spo2="94",
            attending_doctor=doc, disposition="IN_TREATMENT", created_by=recep)
        EmergencyCase.objects.create(
            patient=patients[2], triage="YELLOW", chief_complaint="Road accident — leg injury",
            mode_of_arrival="POLICE", is_mlc=True, mlc_no="MLC-2026-014",
            brought_by="Rescue 1122", attending_doctor=doc, disposition="WAITING",
            created_by=recep)
        EmergencyCase.objects.create(
            patient=patients[7], triage="GREEN", chief_complaint="Fever and headache",
            mode_of_arrival="WALKIN", disposition="WAITING", created_by=recep)

    def _maternity(self, patients, docs, users):
        from datetime import time
        from maternity.models import Pregnancy, AntenatalVisit, Delivery, Birth
        today = timezone.localdate()
        doc_gyn = docs[1]
        reg = users["DOCTOR"]
        preg = Pregnancy.objects.create(
            mother=patients[1], husband_name="Mr. Ahmed", lmp=today - timedelta(days=200),
            gravida=2, para=1, blood_group="O+", registered_by=reg)
        AntenatalVisit.objects.create(
            pregnancy=preg, weight="68 kg", bp="110/70", fundal_height="28 cm",
            fhr="146 bpm", complaints="Mild back pain",
            next_visit=today + timedelta(days=28), seen_by=doc_gyn)
        preg2 = Pregnancy.objects.create(
            mother=patients[5], husband_name="Mr. Bilal", lmp=today - timedelta(days=281),
            gravida=1, para=0, blood_group="A+", status="DELIVERED", registered_by=reg)
        delivery = Delivery.objects.create(
            pregnancy=preg2, mother=patients[5], delivery_type="NORMAL", outcome="LIVE",
            conducted_by=doc_gyn, notes="Spontaneous vaginal delivery, no complications.")
        Birth.objects.create(delivery=delivery, sex="F", weight_kg=Decimal("3.20"),
                             birth_time=time(4, 30), status="ALIVE")

    def _hr(self, users):
        from hr.models import StaffProfile, Attendance, LeaveRequest, SalaryPayment
        today = timezone.localdate()
        admin = users["ADMIN"]
        for role, desig, sal in [
                ("DOCTOR", "Senior Medical Officer", 250000),
                ("NURSE", "Staff Nurse", 60000),
                ("PHARMACIST", "Pharmacist", 80000),
                ("RECEPTIONIST", "Front Desk Officer", 45000)]:
            StaffProfile.objects.get_or_create(user=users[role], defaults=dict(
                designation=desig, monthly_salary=Decimal(sal),
                joining_date=today - timedelta(days=400)))
        for role, st in [("DOCTOR", "PRESENT"), ("NURSE", "PRESENT"),
                         ("PHARMACIST", "PRESENT"), ("RECEPTIONIST", "LEAVE")]:
            Attendance.objects.get_or_create(user=users[role], date=today,
                                             defaults=dict(status=st))
        LeaveRequest.objects.create(
            user=users["NURSE"], leave_type="SICK", start_date=today + timedelta(days=3),
            end_date=today + timedelta(days=4), reason="Fever", status="PENDING")
        SalaryPayment.objects.create(
            user=users["PHARMACIST"], period="Last month", basic=Decimal("80000"),
            allowances=Decimal("5000"), deductions=Decimal("2000"), method="BANK",
            note="Monthly salary", paid_by=admin)

    def _referral(self, patients, docs, users):
        from referral.models import Referral
        Referral.objects.create(
            patient=patients[4], direction="OUT", facility="Lady Reading Hospital, Peshawar",
            department="Cardiology", referring_doctor=docs[0],
            reason="Needs angiography + cardiology opinion",
            clinical_summary="52 y/o male, chest pain, ECG changes. Stabilised.",
            urgency="URGENT", status="PENDING", created_by=users["DOCTOR"])
        Referral.objects.create(
            patient=patients[3], direction="IN", facility="Basic Health Unit, Nowshera",
            reason="Referred for paediatric assessment", urgency="ROUTINE",
            status="ACCEPTED", created_by=users["RECEPTIONIST"])

    def _certificates(self, patients, docs, users):
        from datetime import time
        from certificates.models import BirthCertificate, DeathCertificate
        today = timezone.localdate()
        BirthCertificate.objects.create(
            child_name="Baby Girl Iqbal", sex="F", date_of_birth=today - timedelta(days=1),
            time_of_birth=time(4, 30), place_of_birth="Sehatyar Demo Hospital",
            weight_kg=Decimal("3.20"), mother_name="Maryam Iqbal",
            father_name="Bilal Iqbal", address="Model Town",
            registered_by=users["RECEPTIONIST"])
        DeathCertificate.objects.create(
            deceased_name="Late Abdul Sattar", sex="M", age_text="78 years",
            date_of_death=today - timedelta(days=2), place_of_death="Sehatyar Demo Hospital",
            cause_of_death="Cardiorespiratory failure", attending_doctor=docs[0],
            next_of_kin="Imran Sattar (son)", registered_by=users["ADMIN"])

    def _bloodbank(self, patients, users):
        from bloodbank.models import BloodDonor, BloodUnit, BloodIssue
        today = timezone.localdate()
        labtech = users["LABTECH"]
        donors = []
        for name, cnic, phone, bg in [
                ("Junaid Aslam", "17301-1234567-1", "0300-1234567", "O+"),
                ("Sadia Malik", "17301-7654321-2", "0301-7654321", "A+"),
                ("Rehan Sheikh", "17301-1112223-3", "0302-1112223", "B+")]:
            donors.append(BloodDonor.objects.create(
                full_name=name, cnic=cnic, phone=phone, blood_group=bg,
                last_donation_date=today - timedelta(days=60)))
        units = []
        for bag, bg, donor in [
                ("BAG-O+-01", "O+", donors[0]), ("BAG-O+-02", "O+", None),
                ("BAG-A+-01", "A+", donors[1]), ("BAG-B+-01", "B+", donors[2]),
                ("BAG-AB+-01", "AB+", None)]:
            units.append(BloodUnit.objects.create(
                bag_number=bag, blood_group=bg, component="WHOLE", donor=donor,
                donation_date=today - timedelta(days=5),
                expiry_date=today + timedelta(days=30), screening_done=True,
                status="AVAILABLE", created_by=labtech))
        u = units[0]
        u.status = "ISSUED"; u.save(update_fields=["status"])
        BloodIssue.objects.create(unit=u, patient=patients[4], cross_match="Compatible",
                                  notes="Transfused 1 unit", issued_by=labtech)

    def _vaccination(self, patients, users):
        from django.core.management import call_command
        from vaccination.models import Vaccine, VaccinationRecord
        call_command("seed_epi")
        today = timezone.localdate()
        nurse = users["NURSE"]
        child = patients[3]
        picks = ["BCG", "OPV0", "PENTA1"]
        for i, code in enumerate(picks):
            v = Vaccine.objects.filter(code=code).first()
            if v:
                VaccinationRecord.objects.create(
                    patient=child, vaccine=v, dose_number=1,
                    date_given=today - timedelta(days=30 * (len(picks) - i)),
                    batch_no=f"B{i + 1}23", given_by="Nurse Ayesha",
                    next_due_date=today + timedelta(days=14), created_by=nurse)

    def _consent(self, patients, docs, users):
        from consent.models import ConsentTemplate, ConsentForm
        tpl = ConsentTemplate.objects.create(
            title="General Surgery Consent", consent_type="SURGERY",
            body="I hereby give my consent for the surgical procedure explained to me, "
                 "including the administration of anaesthesia, and I understand its "
                 "risks and expected benefits.")
        ConsentTemplate.objects.create(
            title="Blood Transfusion Consent", consent_type="BLOOD",
            body="I consent to the transfusion of blood / blood products as may be "
                 "medically required during my treatment.")
        ConsentForm.objects.create(
            patient=patients[6], template=tpl, consent_type="SURGERY",
            title="General Surgery Consent", body=tpl.body, procedure_name="Appendectomy",
            doctor=docs[2], signed_by="Bilal Ahmed", relation="Self",
            witness_name="Nurse Ayesha", created_by=users["ADMIN"])
