"""
Seed the system with realistic demo data for testing:
users (every role), suppliers, customers, medicines (with batches + images),
purchases (supplier payables), sales (retail/wholesale/credit, backdated),
stock adjustments, a purchase return, payments, and hospital-side patients/
doctors/appointments.

Re-runnable: it first removes previously seeded rows (identified by markers),
then rebuilds. It never touches non-seed data.

    python manage.py seed_demo
"""
import io
import random
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


PASSWORD = "pharma123"

SEED_USERS = [
    ("admin@pharmadost.com", "ADMIN", "System Admin", True),
    ("reception@pharmadost.com", "RECEPTIONIST", "Sadia Reception", False),
    ("doctor@pharmadost.com", "DOCTOR", "Dr. Imran Khan", False),
    ("pharmacist@pharmadost.com", "PHARMACIST", "Bilal Pharmacist", False),
    ("wholesale@pharmadost.com", "WHOLESALE", "Kamran Wholesale", False),
    ("labtech@pharmadost.com", "LABTECH", "Zheng Lab", False),
    ("sonographer@pharmadost.com", "SONOGRAPHER", "Ayesha Sono", False),
    ("accountant@pharmadost.com", "ACCOUNTANT", "Nadeem Accounts", False),
]

# name, generic, brand, manufacturer, category, retail, wholesale, upp, pack, rack, reorder
MEDS = [
    ("Panadol", "Paracetamol", "Panadol", "GSK", "TABLET", 30, 25, 10, "10x10", "R1", 20),
    ("Augmentin 625", "Amoxicillin+Clav", "Augmentin", "GSK", "TABLET", 250, 230, 6, "1x6", "R2", 10),
    ("Brufen 400", "Ibuprofen", "Brufen", "Abbott", "TABLET", 40, 34, 10, "10x10", "R1", 20),
    ("Calpol Syrup", "Paracetamol", "Calpol", "GSK", "SYRUP", 90, 80, 1, "90ml", "R3", 8),
    ("Ventolin Inhaler", "Salbutamol", "Ventolin", "GSK", "INHALER", 450, 420, 1, "1 unit", "R4", 5),
    ("Risek 20", "Omeprazole", "Risek", "Getz", "CAPSULE", 220, 200, 14, "2x14", "R2", 10),
    ("Amoxil 500", "Amoxicillin", "Amoxil", "GSK", "CAPSULE", 180, 165, 10, "10x10", "R2", 12),
    ("Flagyl 400", "Metronidazole", "Flagyl", "Sanofi", "TABLET", 60, 54, 10, "10x10", "R1", 10),
    ("Nims Syrup", "Nimesulide", "Nims", "Barrett", "SYRUP", 110, 100, 1, "60ml", "R3", 6),
    ("Insulin Novomix", "Insulin", "Novomix", "NovoNordisk", "INJECTION", 1300, 1250, 1, "1 pen", "Fridge", 4),
    ("ORS Sachet", "ORS", "ORS", "Searle", "SACHET", 20, 17, 1, "1 sachet", "R6", 30),
    ("Dettol Cream", "Chlorhexidine", "Dettol", "Reckitt", "CREAM", 150, 135, 1, "30g", "R6", 8),
]

SUPPLIERS = [
    ("Muller & Phipps", "0300-1112233", "Karachi"),
    ("United Distributors", "0301-4455667", "Lahore"),
    ("Pharma Bureau", "0302-7788990", "Islamabad"),
]

PALETTE = ["#4f46e5", "#0ea5a4", "#db2777", "#ea580c", "#2563eb", "#16a34a", "#9333ea", "#dc2626"]

PATIENTS = [
    ("Ahmed Ali", "M", "0311-1111111", 34, "B+", ""),
    ("Fatima Bibi", "F", "0311-2222222", 28, "O+", "Penicillin"),
    ("Usman Tariq", "M", "0311-3333333", 45, "A+", ""),
    ("Zainab Noor", "F", "0311-4444444", 6, "AB+", "Sulpha drugs"),
    ("Hassan Raza", "M", "0311-5555555", 52, "B-", ""),
    ("Maryam Iqbal", "F", "0311-6666666", 19, "O-", ""),
]


class Command(BaseCommand):
    help = "Seed realistic demo data (re-runnable)."

    def handle(self, *args, **opts):
        rnd = random.Random(42)
        with transaction.atomic():
            self._reset()
            users = self._users()
            suppliers = self._suppliers()
            meds = self._medicines()
            self._purchases(meds, suppliers, users, rnd)
            customers = self._customers()
            patients = self._hospital(users, rnd)   # patients first so sales can link to them
            self._sales(meds, customers, users, rnd, patients)
            self._adjustments_returns(meds, suppliers, users)
            self._payments(suppliers, customers, users)
            self._expenses(users, rnd)
        self.stdout.write(self.style.SUCCESS(
            "Demo data seeded. All users password: '%s'. Logins:" % PASSWORD))
        for email, role, *_ in SEED_USERS:
            self.stdout.write(f"  {role:12} {email}")

    # ------------------------------------------------------------------ reset
    def _reset(self):
        from django.contrib.auth import get_user_model
        from sales.models import Sale
        from inventory.models import (Medicine, PurchaseOrder, PurchaseReturn,
                                      StockAdjustment)
        from suppliers.models import Supplier, SupplierPayment
        from customers.models import Customer, CustomerPayment
        from patients.models import Patient
        from opd.models import Doctor, Appointment

        U = get_user_model()
        seed_emails = [e for e, *_ in SEED_USERS]
        seed_users = list(U.objects.filter(email__in=seed_emails))

        Sale.objects.filter(cashier__in=seed_users).delete()            # cascades SaleItem
        PurchaseReturn.objects.filter(notes__startswith="[seed]").delete()
        StockAdjustment.objects.filter(notes__startswith="[seed]").delete()
        SupplierPayment.objects.filter(notes__startswith="[seed]").delete()
        CustomerPayment.objects.filter(notes__startswith="[seed]").delete()
        PurchaseOrder.objects.filter(invoice_number__startswith="SEED-").delete()  # cascades PurchaseItem
        from billing.models import Invoice, Expense, CashClosing
        Invoice.objects.filter(patient__mrn__startswith="SEED-").delete()  # cascades InvoiceItem
        Expense.objects.filter(description__startswith="[seed]").delete()
        CashClosing.objects.filter(note__startswith="[seed]").delete()
        Appointment.objects.filter(patient__mrn__startswith="SEED-").delete()
        Doctor.objects.filter(user__in=seed_users).delete()  # cascades DoctorPayout
        # also remove seeded doctors that have no linked user (else they pile up)
        Doctor.objects.filter(pmdc_no__in=["PMDC-1001", "PMDC-1002"]).delete()
        # medicines (cascades their StockBatch once protected refs above are gone);
        # remove image files first so re-runs don't leak media
        seed_meds = Medicine.all_objects.filter(barcode__startswith="SEED-")
        for m in seed_meds:
            if m.image:
                m.image.delete(save=False)
        seed_meds.delete()
        Patient.objects.filter(mrn__startswith="SEED-").delete()
        Customer.objects.filter(phone__startswith="092").delete()
        Supplier.objects.filter(name__in=[n for n, *_ in SUPPLIERS]).delete()
        # keep the user accounts (just reset below), don't delete admin

    # ------------------------------------------------------------------ users
    def _users(self):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        out = {}
        for email, role, name, is_super in SEED_USERS:
            u = U.objects.filter(email=email).first()
            if not u:
                if is_super:
                    u = U.objects.create_superuser(email=email, password=PASSWORD)
                else:
                    u = U.objects.create_user(email=email, password=PASSWORD)
            else:
                u.set_password(PASSWORD)
            u.role = role
            if is_super:
                u.is_staff = u.is_superuser = True
            parts = name.split()
            u.first_name = parts[0]
            u.last_name = " ".join(parts[1:])
            u.save()
            out[role] = u
        return out

    # -------------------------------------------------------------- suppliers
    def _suppliers(self):
        from suppliers.models import Supplier
        return [Supplier.objects.create(name=n, phone=p, address=a, balance=Decimal("0"))
                for n, p, a in SUPPLIERS]

    def _make_image(self, med, idx):
        from PIL import Image, ImageDraw
        color = PALETTE[idx % len(PALETTE)]
        img = Image.new("RGB", (240, 240), color)
        d = ImageDraw.Draw(img)
        initials = "".join(w[0] for w in med.name.split()[:2]).upper()
        # draw big-ish initials near centre (default font)
        d.text((96, 105), initials, fill="white")
        d.text((14, 214), med.get_category_display(), fill="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return ContentFile(buf.getvalue())

    # -------------------------------------------------------------- medicines
    def _medicines(self):
        from inventory.models import Medicine
        today = timezone.localdate()
        meds = []
        for i, (name, gen, brand, mfr, cat, retail, ws, upp, pack, rack, reorder) in enumerate(MEDS):
            m = Medicine.all_objects.create(
                name=name, generic_name=gen, brand=brand, manufacturer=mfr,
                category=cat, barcode=f"SEED-{i+1:04d}", pack_size=pack,
                units_per_pack=upp, rack_location=rack,
                price=Decimal(retail), wholesale_price=Decimal(ws),
                reorder_level=reorder, quantity=0,
                expiry_date=today + timedelta(days=365),
            )
            m.image.save(f"seed_{i+1}.png", self._make_image(m, i), save=True)
            meds.append(m)
        return meds

    # -------------------------------------------------------------- purchases
    def _purchases(self, meds, suppliers, users, rnd):
        from inventory.models import PurchaseOrder, PurchaseItem
        today = timezone.localdate()
        admin = users["ADMIN"]

        # expiry / stock scenarios keyed by medicine index
        # normal future, a couple near-expiry, one expired, a couple low-stock
        plans = {
            0: [(300, today + timedelta(days=400))],       # Panadol healthy
            1: [(60, today + timedelta(days=200))],
            2: [(180, today + timedelta(days=300))],
            3: [(40, today + timedelta(days=20))],          # Calpol near expiry
            4: [(3, today + timedelta(days=250))],          # Ventolin LOW (<5)
            5: [(120, today + timedelta(days=400))],
            6: [(90, today + timedelta(days=180))],
            7: [(50, today - timedelta(days=10))],          # Flagyl EXPIRED batch
            8: [(30, today + timedelta(days=25))],          # Nims near expiry
            9: [(2, today + timedelta(days=300))],          # Insulin LOW (<4)
            10: [(200, today + timedelta(days=400))],
            11: [(40, today + timedelta(days=400))],
        }
        for i, m in enumerate(meds):
            supplier = suppliers[i % len(suppliers)]
            po = PurchaseOrder.objects.create(
                supplier=supplier, invoice_number=f"SEED-INV-{i+1:03d}",
                created_by=admin,
            )
            total = Decimal("0.00")
            for j, (qty, expiry) in enumerate(plans.get(i, [(50, today + timedelta(days=365))])):
                cost = (m.price * Decimal("0.6")).quantize(Decimal("0.01"))
                PurchaseItem.objects.create(
                    order=po, medicine=m, batch_number=f"B{i+1}-{j+1}",
                    quantity=qty, cost_price=cost, expiry_date=expiry,
                )
                m.add_stock(qty, batch_number=f"B{i+1}-{j+1}", expiry_date=expiry,
                            cost_price=cost, supplier=supplier)
                total += cost * qty
                # keep medicine.expiry_date as earliest batch (for legacy display)
                if expiry < m.expiry_date:
                    m.expiry_date = expiry
                    m.save(update_fields=["expiry_date"])
            # pay ~70% now -> leaves a payable
            paid = (total * Decimal("0.7")).quantize(Decimal("0.01"))
            po.total = total
            po.paid = paid
            po.save(update_fields=["total", "paid"])
            supplier.balance += (total - paid)
            supplier.save(update_fields=["balance"])

    # -------------------------------------------------------------- customers
    def _customers(self):
        from customers.models import Customer
        data = [
            ("RETAIL", "Regular Walk-in Khata", "", "0921000001", "Model Town", 50000),
            ("RETAIL", "Iqbal Sahib", "", "0921000002", "Gulberg", 50000),
            ("WHOLESALE", "City Medical Store", "City Medical Store", "0922000001", "Saddar", 200000),
            ("WHOLESALE", "Al-Shifa Pharmacy", "Al-Shifa Pharmacy", "0922000002", "Cantt", 150000),
            ("WHOLESALE", "New Care Chemist", "New Care Chemist", "0922000003", "DHA", 100000),
        ]
        out = []
        for typ, name, shop, phone, area, limit in data:
            out.append(Customer.objects.create(
                type=typ, name=name, shop_name=shop, phone=phone, area=area,
                credit_limit=Decimal(limit), balance=Decimal("0"),
            ))
        return out

    # ------------------------------------------------------------------ sales
    def _sales(self, meds, customers, users, rnd, patients=None):
        from sales.services import create_sale
        from sales.models import Sale
        patients = patients or []
        pharmacist = users["PHARMACIST"]
        wholesaler = users["WHOLESALE"]
        retail_customers = [c for c in customers if c.type == "RETAIL"]
        wholesale_customers = [c for c in customers if c.type == "WHOLESALE"]
        sellable = [m for m in meds if not m.is_expired and m.quantity > 5]

        def backdate(sale, days_ago):
            sale.created_at = timezone.now() - timedelta(days=days_ago,
                                                         hours=rnd.randint(0, 8))
            sale.save(update_fields=["created_at"])

        def cash_sale(items):
            # link ~half of walk-in retail sales to a registered patient
            pat = rnd.choice(patients) if patients and rnd.random() < 0.5 else None
            return create_sale(items=items, sale_type=Sale.RETAIL,
                               customer_name="Walk-in", payment_method="CASH",
                               cashier=pharmacist, patient=pat)

        # ~24 sales spread over the last 21 days
        for n in range(24):
            days_ago = rnd.randint(0, 21)
            picks = rnd.sample(sellable, rnd.randint(1, 3))
            items = [{"medicine_id": m.id, "quantity": rnd.randint(1, 4)} for m in picks]
            try:
                if n % 3 == 0:
                    # wholesale credit sale (half paid)
                    cust = rnd.choice(wholesale_customers)
                    total_guess = sum(m.wholesale_price * 3 for m in picks)
                    sale = create_sale(items=items, sale_type=Sale.WHOLESALE, customer=cust,
                                       paid=(total_guess * Decimal("0.5")).quantize(Decimal("0.01")),
                                       payment_method="CREDIT", cashier=wholesaler)
                elif n % 3 == 1:
                    # retail credit (khata)
                    cust = rnd.choice(retail_customers)
                    sale = create_sale(items=items, sale_type=Sale.RETAIL, customer=cust,
                                       paid=Decimal("0.00"), payment_method="CREDIT",
                                       cashier=pharmacist)
                else:
                    sale = cash_sale(items)
            except ValueError:
                # e.g. credit limit reached or stock ran out -> fall back to cash
                try:
                    sale = cash_sale(items)
                except ValueError:
                    continue
            backdate(sale, days_ago)

    # ------------------------------------------------- adjustments & returns
    def _adjustments_returns(self, meds, suppliers, users):
        from inventory.models import StockBatch
        from inventory.services import apply_adjustment, create_purchase_return
        admin = users["ADMIN"]

        # damage write-off on Brufen (index 2)
        b = StockBatch.objects.filter(medicine=meds[2], quantity__gt=5).first()
        if b:
            apply_adjustment(batch=b, qty_change=-4, reason="DAMAGE",
                             notes="[seed] broken strips", by_user=admin)

        # stock count correction on Panadol (index 0)
        b0 = StockBatch.objects.filter(medicine=meds[0], quantity__gt=0).first()
        if b0:
            apply_adjustment(batch=b0, qty_change=6, reason="COUNT",
                             notes="[seed] found extra on shelf", by_user=admin)

        # return the EXPIRED Flagyl batch (index 7) to its supplier
        eb = StockBatch.objects.filter(medicine=meds[7], quantity__gt=0).first()
        if eb:
            create_purchase_return(
                supplier=meds[7].supplier, reason="EXPIRY",
                notes="[seed] expired stock returned",
                items=[{"batch_id": eb.id, "quantity": min(20, eb.quantity)}],
                by_user=admin,
            )

    # --------------------------------------------------------------- payments
    def _payments(self, suppliers, customers, users):
        from suppliers.services import record_supplier_payment
        from customers.services import record_payment
        admin = users["ADMIN"]

        # pay down first supplier a bit
        if suppliers and suppliers[0].balance > 0:
            record_supplier_payment(suppliers[0], amount=min(Decimal("2000"), suppliers[0].balance),
                                    method="BANK", notes="[seed] part payment", by_user=admin)

        # a wholesale customer clears part of their khata (refresh: balance grew during sales)
        for c in customers:
            c.refresh_from_db()
            if c.type == "WHOLESALE" and c.balance > 0:
                record_payment(c, amount=(c.balance / 2).quantize(Decimal("0.01")),
                               method="CASH", notes="[seed] khata payment",
                               received_by=admin)
                break

    # --------------------------------------------------------------- hospital
    def _expenses(self, users, rnd):
        from billing.models import Expense
        admin_user = users.get("ADMIN") or users.get("ACCOUNTANT")
        today = timezone.localdate()
        rows = [
            ("RENT", "[seed] Monthly shop rent", Decimal("35000"), 2),
            ("SALARY", "[seed] Staff salaries", Decimal("48000"), 1),
            ("UTILITIES", "[seed] Electricity bill", Decimal("9800"), 3),
            ("SUPPLIES", "[seed] Packaging bags & printer roll", Decimal("2400"), 0),
            ("MAINTENANCE", "[seed] AC servicing", Decimal("3500"), 5),
            ("UTILITIES", "[seed] Internet & phone", Decimal("4200"), 1),
        ]
        for cat, desc, amt, days_ago in rows:
            Expense.objects.create(
                date=today - timedelta(days=days_ago), category=cat,
                description=desc, amount=amt, payment_method="CASH",
                recorded_by=admin_user)

        # a sample cash closing for yesterday so the list isn't empty
        from billing.models import CashClosing
        from billing.services import cash_position
        yday = today - timedelta(days=1)
        if not CashClosing.objects.filter(date=yday).exists():
            pos = cash_position(yday)
            opening = Decimal("5000")
            expected = opening + pos["net"]
            CashClosing.objects.create(
                date=yday, opening=opening, cash_in=pos["cash_in"],
                cash_out=pos["cash_out"], expected=expected,
                counted=expected, difference=Decimal("0.00"),
                note="[seed] day-end", closed_by=admin_user)

    def _hospital(self, users, rnd):
        from patients.models import Patient
        from opd.models import Doctor, Appointment
        today = timezone.localdate()

        patients = []
        for i, (name, gender, phone, age, bg, allergy) in enumerate(PATIENTS):
            patients.append(Patient.objects.create(
                mrn=f"SEED-{2026}-{i+1:04d}", full_name=name, gender=gender,
                phone=phone, age_years=age, blood_group=bg, allergies=allergy,
            ))

        doc_user = users["DOCTOR"]
        d1 = Doctor.objects.create(user=doc_user, full_name="Dr. Imran Khan",
                                   specialty="General Physician", pmdc_no="PMDC-1001",
                                   opd_fee=Decimal("1000"), followup_fee=Decimal("500"),
                                   share_percent=Decimal("70"))
        d2 = Doctor.objects.create(full_name="Dr. Sara Ahmed", specialty="Gynaecologist",
                                   pmdc_no="PMDC-1002", opd_fee=Decimal("1500"),
                                   followup_fee=Decimal("700"), share_percent=Decimal("60"))
        docs = [d1, d2]

        from billing.services import create_service_invoice
        for i, p in enumerate(patients):
            doc = docs[i % 2]
            appt = Appointment.objects.create(
                patient=p, doctor=doc,
                appointment_date=today - timedelta(days=rnd.randint(0, 3)),
                visit_type="OPD",
                status=rnd.choice(["BOOKED", "DONE", "ARRIVED"]),
            )
            # bill the consultation fee (about half already collected, half pending)
            create_service_invoice(
                patient=p, appointment=appt, created_by=doc_user,
                items=[(f"OPD Consultation — {doc.full_name}", doc.opd_fee)],
                paid=(doc.opd_fee if i % 2 == 0 else Decimal("0.00")))

        # a couple of clinical-history records so patient history isn't empty
        from opd.models import ClinicalRecord
        ClinicalRecord.objects.create(
            patient=patients[0], doctor=d1, record_type="CONSULT",
            title="Fever & body aches", diagnosis="Viral fever",
            details="Advised rest, fluids, paracetamol.", bp="118/76", pulse="88",
            temperature="101 F", weight="72 kg", created_by=doc_user)
        ClinicalRecord.objects.create(
            patient=patients[2], doctor=d2, record_type="ULTRASOUND",
            title="Abdominal Ultrasound", diagnosis="Mild fatty liver",
            details="Liver mildly enlarged, no focal lesion. Advised follow-up.",
            created_by=doc_user)

        # imaging / radiology studies so the Imaging module + history isn't empty
        from imaging.models import ImagingStudy
        from billing.services import create_service_invoice
        admin_user = users.get("ADMIN") or doc_user
        sono = users.get("SONOGRAPHER")
        us1 = ImagingStudy.objects.create(
            patient=patients[2], modality="ULTRASOUND",
            study_name="Abdominal Ultrasound (Complete)",
            clinical_note="Right upper quadrant pain, r/o gallstones.",
            referred_by=doc_user, performed_by=sono, status="Reported",
            price=Decimal("1200"),
            findings="Liver mildly enlarged with increased echogenicity. "
                     "Gallbladder normal, no calculi. CBD not dilated. "
                     "Both kidneys normal size and echotexture. No free fluid.",
            impression="Grade I fatty liver. No cholelithiasis.")
        xr1 = ImagingStudy.objects.create(
            patient=patients[0], modality="XRAY",
            study_name="Chest X-Ray (PA view)",
            clinical_note="Cough with fever x 4 days.",
            referred_by=doc_user, performed_by=sono, status="Reported",
            price=Decimal("800"),
            findings="Both lung fields clear. No consolidation or effusion. "
                     "Cardiac silhouette normal. Costophrenic angles clear.",
            impression="Normal chest radiograph.")
        # service bills for the scans — one unpaid (pending), one already collected
        create_service_invoice(
            patient=us1.patient,
            items=[(f"{us1.get_modality_display()}: {us1.study_name}", us1.price)],
            created_by=admin_user)  # unpaid
        create_service_invoice(
            patient=xr1.patient,
            items=[(f"{xr1.get_modality_display()}: {xr1.study_name}", xr1.price)],
            created_by=admin_user, paid=xr1.price)  # collected

        # a doctor payout so the payout ledger isn't empty (partial settlement)
        from opd.models import DoctorPayout
        DoctorPayout.objects.create(
            doctor=d1, date=today - timedelta(days=1), amount=Decimal("1000"),
            payment_method="CASH", note="Weekly settlement", paid_by=admin_user)
        ImagingStudy.objects.create(
            patient=patients[4 % len(patients)], modality="ULTRASOUND",
            study_name="Obstetric Ultrasound (Dating)",
            clinical_note="LMP uncertain, confirm gestational age.",
            referred_by=None, performed_by=sono, status="Pending",
            price=Decimal("1500"))

        return patients
