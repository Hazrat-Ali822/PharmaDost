"""Apply a queued offline action by reusing the *exact* forms and services the
online views use.

The whole safety of the offline layer rests on this: an offline visit is bound to
the same `PatientForm` + `VisitForm`, validated by the same `clean()` methods and
booked through the same `_book_visit` as one typed live at the desk. Anything
with real consequences — an admission taking a bed, a dose leaving stock, a
discharge raising a bill — goes through the app's own service function, so there
is no second, looser code path that could let bad data in just because it
arrived late.

Each handler is called inside a `transaction.atomic()` by the sync view and must
either return a JSON-serialisable result dict (echoed back to the client so it
can show the server-assigned MRN / token) or raise:

  * ``ValidationError`` / ``PermissionDenied`` / ``Http404`` -> a *permanent*
    rejection; the row is filed FAILED and shown on the outbox screen for the
    desk to re-enter. Retrying will never make it work.
  * any other exception -> treated as *transient* by the view; nothing is
    recorded and the client retries the action next time it is online.

**A form can only be marked `data-offline-kind` if everything it needs is in the
form.** Where the online view takes the parent from the URL — the appointment a
prescription hangs off, the patient a clinical record belongs to — the template
must carry it as a hidden input, or the replay has nothing to attach the record
to. `_parent()` below turns a missing one into a readable permanent failure
rather than a database error that retries for ever.
"""
from django.core.exceptions import PermissionDenied, ValidationError

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _require(user, feature):
    """Offline replay must respect the same feature gates as the live view — a
    queued action is still that user acting, just deferred."""
    if not (user.is_superuser or user.has_feature(feature)):
        raise PermissionDenied(f"You do not have access to '{feature}'.")


def _parent(model, pk, label, required=True):
    """Resolve the record an offline entry hangs off, through the model's own
    (tenant-scoped) manager.

    A blank or unknown id is a *permanent* failure: the entry can never apply, so
    it belongs on the outbox screen where someone can re-enter it, not in a retry
    loop that looks like it is still syncing.
    """
    if not pk:
        if required:
            raise ValidationError(
                f"This entry was saved without a {label}, so it cannot be filed. "
                f"Please enter it again.")
        return None
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        raise ValidationError(f"The {label} this entry refers to no longer exists.")
    return obj


def _valid(form):
    """Bind-and-check, raising the same errors the live screen would show."""
    if not form.is_valid():
        raise ValidationError(form.errors)
    return form


def _one(value):
    """A form array key (medicine_id[]) arrives as a list when repeated, but as a
    bare value when a single row was submitted — normalise to a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(data, key):
    return (data.get(key) or "").strip()


# --------------------------------------------------------------------------
# reception / patients
# --------------------------------------------------------------------------


def handle_visit(request, data):
    """Register a walk-in (if new) and book the visit — mirrors
    ``opd.views.visit_create`` field-for-field."""
    from patients.forms import PatientForm
    from patients.models import Patient
    from opd.forms import VisitForm
    from opd.views import _book_visit

    _require(request.user, "appointments")

    patient_id = data.get("patient_id")
    is_new = not patient_id

    visit_form = VisitForm(data, user=request.user)
    patient_form = PatientForm(data) if is_new else None

    errors = {}
    if not visit_form.is_valid():
        errors.update(visit_form.errors)
    if patient_form is not None and not patient_form.is_valid():
        errors.update(patient_form.errors)
    if errors:
        raise ValidationError(errors)

    patient = (patient_form.save() if is_new
               else _parent(Patient, patient_id, "patient"))
    appointment = _book_visit(request, patient, visit_form.cleaned_data)
    return {
        "patient_id": patient.pk,
        "mrn": patient.mrn,
        "patient_name": patient.full_name,
        "appointment_id": appointment.pk,
        "token_no": appointment.token_no,
        "doctor": appointment.doctor.full_name,
    }


def handle_patient(request, data):
    """Register a new patient while offline — mirrors patients.views.patient_create.

    The MRN comes from the locked per-hospital counter at replay time, never from
    the device (see CLAUDE.md); the slip printed offline is provisional.
    """
    from patients.forms import PatientForm
    _require(request.user, "patients")
    patient = _valid(PatientForm(data)).save()
    return {"patient_id": patient.pk, "mrn": patient.mrn,
            "full_name": patient.full_name}


def handle_patient_record(request, data):
    """Add a clinical history entry — mirrors patients.views.record_add."""
    from patients.forms import ClinicalRecordForm
    from patients.models import Patient
    _require(request.user, "patients")
    patient = _parent(Patient, data.get("patient_id"), "patient")
    record = _valid(ClinicalRecordForm(data)).save(commit=False)
    record.patient = patient
    record.created_by = request.user
    record.save()
    return {"record_id": record.id, "patient_id": patient.pk, "title": record.title}


# --------------------------------------------------------------------------
# OPD
# --------------------------------------------------------------------------


def handle_appointment(request, data):
    """Book an appointment — mirrors ``opd.views.appointment_create``.

    Goes through `bill_and_notify` so the consultation invoice is raised and the
    doctor notified, exactly as online. The token is assigned by
    `Appointment.save()` at replay time, so two devices booking offline cannot
    hand out the same number.
    """
    from opd.forms import AppointmentForm
    from opd.services import bill_and_notify
    _require(request.user, "appointments")
    appointment = bill_and_notify(_valid(AppointmentForm(data, user=request.user)).save(), request.user)
    return {"appointment_id": appointment.id, "token_no": appointment.token_no,
            "patient": appointment.patient.full_name}


def handle_department(request, data):
    """Add a department — mirrors ``opd.views.department_list`` (POST)."""
    from opd.forms import DepartmentForm
    _require(request.user, "doctors")
    dept = _valid(DepartmentForm(data)).save()
    return {"department_id": dept.id, "name": dept.name}


def handle_doctor(request, data):
    """Add a doctor — mirrors ``opd.views.doctor_create`` (without the weekly
    timings formset, which the offline form does not carry; timings are added
    from the doctor's page once back online)."""
    from opd.forms import DoctorForm
    _require(request.user, "doctors")
    doctor = _valid(DoctorForm(data)).save()
    return {"doctor_id": doctor.id, "full_name": doctor.full_name}


def handle_payout(request, data):
    """Record a doctor payout — mirrors ``opd.views.payout_doctor``."""
    from opd.forms import DoctorPayoutForm
    from opd.models import Doctor
    _require(request.user, "payouts")
    doctor = _parent(Doctor, data.get("doctor_id"), "doctor")
    payout = _valid(DoctorPayoutForm(data)).save(commit=False)
    payout.doctor = doctor
    payout.paid_by = request.user
    payout.save()
    return {"payout_id": payout.id, "amount": str(payout.amount),
            "doctor": doctor.full_name}


# --------------------------------------------------------------------------
# prescriptions
# --------------------------------------------------------------------------


def handle_prescription(request, data):
    """Write a prescription — mirrors ``prescriptions.views.prescription_create``.

    The medicines travel with it: the item formset is bound from the same posted
    keys, so an Rx written with no signal reaches the pharmacy queue complete,
    not as an empty header.
    """
    from opd.models import Appointment
    from prescriptions.forms import PrescriptionForm, PrescriptionItemFormSet
    from prescriptions.services import save_prescription

    _require(request.user, "prescriptions")
    appointment = _parent(Appointment, data.get("appointment_id"), "appointment")

    form = PrescriptionForm(data)
    formset = PrescriptionItemFormSet(data, prefix="meds")
    errors = {}
    if not form.is_valid():
        errors.update(form.errors)
    if not formset.is_valid():
        errors["medicines"] = [str(e) for e in formset.errors if e] or ["invalid rows"]
    if errors:
        raise ValidationError(errors)

    res = save_prescription(appointment, form, formset, request.user)
    return {"prescription_id": res.prescription.id,
            "medicines": len(res.medicines),
            "lab_tests": res.n_tests, "scans": res.n_scans,
            "warnings": res.warnings}


def handle_rx_preset(request, data):
    """Save an Rx preset — mirrors ``prescriptions.views.preset_create``."""
    from prescriptions.forms import RxPresetForm, RxPresetItemFormSet
    from django.db import transaction
    _require(request.user, "prescriptions")

    form = RxPresetForm(data)
    formset = RxPresetItemFormSet(data, prefix="items")
    if not form.is_valid() or not formset.is_valid():
        raise ValidationError(form.errors or {"items": ["invalid rows"]})

    with transaction.atomic():
        preset = form.save(commit=False)
        if request.user.hospital:
            preset.hospital = request.user.hospital
        else:
            from saas.models import Hospital
            preset.hospital = Hospital.objects.first()
        preset.save()
        formset.instance = preset
        formset.save()
    return {"preset_id": preset.id, "name": preset.name}


# --------------------------------------------------------------------------
# pharmacy / inventory
# --------------------------------------------------------------------------


def handle_sale(request, data):
    """Replay a pharmacy sale taken while offline — mirrors the POST parsing in
    ``sales.views.sale_create``, but with ``on_short="record"`` so a sale made
    against stock that has since run short is recorded and flagged for the
    pharmacist rather than refused (the medicine already left the shelf)."""
    from accounts.models import Notification
    from customers.models import Customer
    from patients.models import Patient
    from sales.models import Sale
    from sales.services import create_sale

    _require(request.user, "pos")

    med_ids = _one(data.get("medicine_id[]"))
    qtys = _one(data.get("quantity[]"))
    prices = _one(data.get("unit_price[]"))
    line_discounts = _one(data.get("line_discount[]"))

    items = []
    for i, m_id in enumerate(med_ids):
        if not m_id:
            continue
        q = _int(qtys[i] if i < len(qtys) else 0)
        if q < 1:
            continue
        item = {"medicine_id": int(m_id), "quantity": q}
        price = prices[i].strip() if i < len(prices) and prices[i] else None
        if price:
            item["unit_price"] = price
        ld = line_discounts[i].strip() if i < len(line_discounts) and line_discounts[i] else None
        if ld:
            item["discount"] = ld
        items.append(item)

    if not items:
        raise ValidationError("The offline sale had no valid line items.")

    sale_type = data.get("sale_type", Sale.RETAIL)
    customer = _parent(Customer, data.get("customer_id") or data.get("customer"),
                       "customer", required=False)
    patient = _parent(Patient, data.get("patient_id"), "patient", required=False)

    if sale_type == getattr(Sale, "WHOLESALE", "WHOLESALE") and customer is None:
        # The live screen blocks this in JS; offline the same rule has to hold on
        # the server, or a queued wholesale bill lands with nobody to invoice.
        raise ValidationError("A wholesale sale needs a registered customer.")

    sale = create_sale(
        items=items,
        sale_type=sale_type,
        customer=customer,
        patient=patient,
        customer_name=_text(data, "customer_name"),
        discount=data.get("discount") or 0,
        paid=data.get("paid"),
        payment_method=data.get("payment_method", "CASH"),
        cashier=request.user,
        on_short="record",
    )

    if sale.needs_reconcile:
        # Exceptional — the pharmacist must verify the physical count. This is one
        # of the few things worth an active notification (see CLAUDE.md), and
        # `force` keeps it unread through the replay: the shelf still needs
        # counting whenever the sale was rung up.
        Notification.send_to_role(
            sale.hospital, "PHARMACIST",
            f"Offline sale #{sale.id} dispensed stock that was short — please "
            f"reconcile: {sale.reconcile_note}", f"/sales/{sale.id}/", force=True)

    return {
        "sale_id": sale.id,
        "total": str(sale.total),
        "needs_reconcile": sale.needs_reconcile,
        "reconcile_note": sale.reconcile_note,
    }


def handle_medicine(request, data):
    """Add a medicine to the catalogue — mirrors ``inventory.views.medicine_create``."""
    from inventory.forms import MedicineForm
    _require(request.user, "inventory")
    medicine = _valid(MedicineForm(data)).save()
    return {"medicine_id": medicine.id, "name": medicine.name}


def handle_adjustment(request, data):
    """Record a stock adjustment — mirrors ``inventory.views.adjustment_create``.

    A negative adjustment is stock leaving with no sale behind it, so it notifies
    the admins here exactly as it does online — shrinkage recorded offline must
    not be quieter than shrinkage recorded at the desk.
    """
    from accounts.models import Notification
    from inventory.models import StockAdjustment, StockBatch
    from inventory.services import apply_adjustment

    _require(request.user, "inventory")
    batch = _parent(StockBatch, data.get("batch"), "stock batch")
    qty_change = _int(data.get("qty_change"))
    if qty_change == 0:
        raise ValidationError("The adjustment quantity was empty.")
    reason = data.get("reason", "OTHER")

    try:
        apply_adjustment(batch=batch, qty_change=qty_change, reason=reason,
                         notes=_text(data, "notes"), by_user=request.user)
    except ValueError as e:
        raise ValidationError(str(e))

    if qty_change < 0:
        Notification.notify_admins(
            hospital=request.user.hospital,
            message=(f"📦 Stock written off: {abs(qty_change)} × "
                     f"{batch.medicine.name} "
                     f"({dict(StockAdjustment.REASON_CHOICES).get(reason, 'Other')}) "
                     f"by {request.user.email}."),
            link='/medicines/adjustments/')
    return {"batch_id": batch.id, "qty_change": qty_change,
            "medicine": batch.medicine.name}


def handle_purchase_return(request, data):
    """Return stock to a supplier — mirrors ``inventory.views.preturn_create``."""
    from inventory.models import StockBatch
    from inventory.services import create_purchase_return
    from suppliers.models import Supplier

    _require(request.user, "inventory")
    batch_ids = _one(data.get("batch_id[]"))
    qtys = _one(data.get("quantity[]"))
    costs = _one(data.get("cost_price[]"))

    items = []
    for i, b_id in enumerate(batch_ids):
        if not b_id:
            continue
        q = _int(qtys[i] if i < len(qtys) else 0)
        if q < 1:
            continue
        item = {"batch_id": int(b_id), "quantity": q}
        c = costs[i].strip() if i < len(costs) and costs[i] else None
        if c:
            item["cost_price"] = c
        items.append(item)

    if not items:
        raise ValidationError("The return had no valid line items.")

    supplier = _parent(Supplier, data.get("supplier"), "supplier", required=False)
    try:
        ret = create_purchase_return(
            supplier=supplier, reason=data.get("reason", "EXPIRY"),
            notes=_text(data, "notes"), items=items, by_user=request.user)
    except (StockBatch.DoesNotExist, ValueError) as e:
        raise ValidationError(str(e))
    return {"return_id": ret.id}


# --------------------------------------------------------------------------
# suppliers & customers
# --------------------------------------------------------------------------


def handle_supplier(request, data):
    """Add a supplier — mirrors ``suppliers.views.supplier_create``."""
    from suppliers.forms import SupplierForm
    _require(request.user, "suppliers")
    supplier = _valid(SupplierForm(data)).save()
    return {"supplier_id": supplier.id, "name": supplier.name}


def handle_supplier_payment(request, data):
    """Pay a supplier — mirrors ``suppliers.views.payment_create``."""
    from suppliers.forms import SupplierPaymentForm
    from suppliers.models import Supplier
    _require(request.user, "suppliers")
    supplier = _parent(Supplier, data.get("supplier_id"), "supplier")
    payment = _valid(SupplierPaymentForm(data)).save(commit=False)
    payment.supplier = supplier
    payment.by_user = request.user
    payment.save()
    return {"payment_id": payment.id, "amount": str(payment.amount),
            "supplier": supplier.name}


def handle_customer(request, data):
    """Add a wholesale customer — mirrors ``customers.views.customer_create``."""
    from customers.forms import CustomerForm
    _require(request.user, "customers")
    customer = _valid(CustomerForm(data)).save()
    return {"customer_id": customer.id, "name": customer.name}


def handle_customer_payment(request, data):
    """Receive a payment against a customer's ledger — mirrors
    ``customers.views.payment_create``."""
    from customers.forms import CustomerPaymentForm
    from customers.models import Customer
    from customers.services import record_payment

    _require(request.user, "customers")
    customer = _parent(Customer, data.get("customer_id"), "customer")
    cd = _valid(CustomerPaymentForm(data)).cleaned_data
    try:
        payment = record_payment(customer, amount=cd["amount"], method=cd["method"],
                                 notes=cd["notes"], received_by=request.user,
                                 date=cd["date"])
    except ValueError as e:
        raise ValidationError(str(e))
    return {"payment_id": getattr(payment, "id", None), "amount": str(cd["amount"]),
            "customer": customer.name}


# --------------------------------------------------------------------------
# lab & imaging
# --------------------------------------------------------------------------


def handle_lab(request, data):
    """Order lab tests — mirrors ``lab.views.order_create``.

    Goes through `create_test_order`, so the pending bill is raised with the
    order exactly as it is online.
    """
    from lab.forms import TestOrderCreateForm
    from lab.services import create_test_order
    _require(request.user, "lab")
    form = _valid(TestOrderCreateForm(data, user=request.user))
    order = create_test_order(form, request.user)
    return {"order_id": order.id, "patient": order.patient.full_name,
            "tests": [r.lab_test.name for r in order.results.select_related("lab_test")],
            "invoice_id": order.invoice_id}


def handle_lab_result(request, data):
    """Enter lab results — mirrors ``lab.views.order_results_edit``."""
    from lab.forms import TestResultFormSet
    from lab.models import TestOrder
    _require(request.user, "lab")
    order = _parent(TestOrder, data.get("order_id"), "lab order")
    formset = TestResultFormSet(data, instance=order)
    if not formset.is_valid():
        raise ValidationError({"results": [str(e) for e in formset.errors if e]
                               or ["invalid results"]})
    formset.save()
    order.status = data.get("status") or "Completed"
    order.save(update_fields=["status"])
    return {"order_id": order.id, "status": order.status}


def handle_lab_test(request, data):
    """Add a test to the price list — mirrors the add branch of
    ``lab.views.test_catalog``."""
    from decimal import Decimal, InvalidOperation
    from lab.models import LabTest, TestCategory
    _require(request.user, "catalog")

    name = _text(data, "name")
    new_cat = _text(data, "new_category")
    if not name:
        raise ValidationError("The test name is required.")
    if new_cat:
        category, _ = TestCategory.objects.get_or_create(name=new_cat)
    else:
        category = _parent(TestCategory, data.get("category"), "category")
    try:
        price = Decimal(data.get("price") or 0)
    except (InvalidOperation, TypeError):
        price = Decimal("0")
    test = LabTest.objects.create(
        category=category, name=name, price=price,
        unit=_text(data, "unit"), normal_range=_text(data, "normal_range"))
    return {"lab_test_id": test.id, "name": test.name}


def handle_imaging(request, data):
    """Register a scan — mirrors ``imaging.views.study_create`` (bill included)."""
    from imaging.forms import ImagingStudyCreateForm
    from imaging.services import create_study
    _require(request.user, "imaging")
    form = _valid(ImagingStudyCreateForm(data, user=request.user))
    study = create_study(form, request.user)
    return {"study_id": study.id, "patient": study.patient.full_name,
            "study_name": study.study_name, "invoice_id": study.invoice_id}


def handle_imaging_report(request, data):
    """Write a radiology report — mirrors ``imaging.views.study_report_edit``.

    The film itself cannot ride in the outbox (an image is not form text), so an
    offline report saves its findings and impression; the image is attached from
    the study page once back online.
    """
    from imaging.forms import ImagingReportForm
    from imaging.models import ImagingStudy
    _require(request.user, "imaging")
    study = _parent(ImagingStudy, data.get("study_id"), "study")
    form = _valid(ImagingReportForm(data, instance=study, user=request.user))
    study = form.save()
    return {"study_id": study.id, "status": study.status}


def handle_scan_type(request, data):
    """Add a scan to the price list — mirrors the add branch of
    ``imaging.views.scan_catalog``."""
    from decimal import Decimal, InvalidOperation
    from imaging.models import ScanType
    _require(request.user, "catalog")
    name = _text(data, "name")
    if not name:
        raise ValidationError("The scan name is required.")
    try:
        price = Decimal(data.get("price") or 0)
    except (InvalidOperation, TypeError):
        price = Decimal("0")
    scan = ScanType.objects.create(
        name=name, modality=data.get("modality", "OTHER"), price=price)
    return {"scan_type_id": scan.id, "name": scan.name}


# --------------------------------------------------------------------------
# IPD (ward)
# --------------------------------------------------------------------------


def handle_ward(request, data):
    """Create a ward — mirrors ``ipd.views.ward_create``."""
    from ipd.forms import WardForm
    _require(request.user, "ipd")
    ward = _valid(WardForm(data)).save()
    return {"ward_id": ward.id, "name": ward.name}


def handle_bed(request, data):
    """Register a bed — mirrors ``ipd.views.bed_create``."""
    from ipd.forms import BedForm
    _require(request.user, "ipd")
    bed = _valid(BedForm(data)).save()
    return {"bed_id": bed.id, "bed_number": bed.bed_number}


def handle_admission(request, data):
    """Admit a patient — mirrors ``ipd.views.admission_create``.

    `admit_patient` re-checks the bed under a lock at replay time, so an offline
    admission against a bed that has since been filled is rejected permanently
    and shown on the outbox screen rather than double-booking the bed.
    """
    from ipd.forms import AdmissionForm
    from ipd.models import AdmissionRequest
    from ipd.services import admit_patient

    _require(request.user, "ipd")
    admission = _valid(AdmissionForm(data, user=request.user)).save(commit=False)
    adm_req = _parent(AdmissionRequest, data.get("request_id"), "admission request",
                      required=False)
    res = admit_patient(admission, request.user,
                        adm_request=adm_req if adm_req and adm_req.status == 'Pending' else None)
    return {"admission_id": res.admission.id, "bed": res.bed.bed_number,
            "patient": res.admission.patient.full_name}


def handle_admission_advise(request, data):
    """A doctor advises admission — mirrors ``ipd.views.admission_advise``."""
    from accounts.models import Notification
    from ipd.models import AdmissionRequest, Ward
    from patients.models import Patient

    _require(request.user, "patients")
    patient = _parent(Patient, data.get("patient_id"), "patient")
    reason = _text(data, "reason")
    if not reason:
        raise ValidationError("Please enter a reason for admission.")
    ward = _parent(Ward, data.get("preferred_ward"), "ward", required=False)
    req = AdmissionRequest.objects.create(
        patient=patient, advised_by=request.user, reason=reason, preferred_ward=ward)
    for role in ("RECEPTIONIST", "ADMIN"):
        Notification.send_to_role(
            hospital=patient.hospital, role=role,
            message=f"🛏️ Admission advised: {patient.full_name} — please allot a bed.",
            link='/ipd/requests/')
    return {"request_id": req.id, "patient": patient.full_name}


def handle_round(request, data):
    """Record a doctor's round — mirrors ``ipd.views.doctor_round_add``."""
    from ipd.forms import DoctorRoundForm
    from ipd.models import Admission
    _require(request.user, "ipd")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    round_log = _valid(DoctorRoundForm(data)).save(commit=False)
    round_log.admission = admission
    round_log.save()
    return {"round_id": round_log.id, "admission_id": admission.id}


def handle_medication(request, data):
    """Log a dose given on the ward — mirrors ``ipd.views.medication_log_add``.

    Runs through `log_medication`, which never refuses: the dose is already
    inside the patient, so a short stock level records the chart entry and flags
    the shortfall instead of losing it (see CLAUDE.md).
    """
    from ipd.forms import MedicationLogForm
    from ipd.models import Admission
    from ipd.services import log_medication

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    form = _valid(MedicationLogForm(data))
    res = log_medication(admission, form.save(commit=False),
                         form.cleaned_data.get("medicine"), request.user)
    return {"log_id": res.log.id, "medicine": res.log.medicine_name,
            "quantity": res.log.quantity, "stock_short": res.stock_short,
            "warnings": res.warnings}


def handle_vital(request, data):
    """Record a nursing vitals set — mirrors ``ipd.views.vitals_add``. Bedside work
    on a ward round is exactly what the outbox is for."""
    from ipd.forms import VitalsObservationForm
    from ipd.models import Admission
    from ipd.services import record_vitals

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    obs = record_vitals(admission, _valid(VitalsObservationForm(data)), request.user)
    return {"observation_id": obs.id, "mews": obs.mews["score"], "band": obs.mews["band"]}


def handle_fluid(request, data):
    """Record a fluid intake/output entry — mirrors ``ipd.views.fluid_add``."""
    from ipd.forms import FluidBalanceEntryForm
    from ipd.models import Admission
    from ipd.services import record_fluid

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    entry = record_fluid(admission, _valid(FluidBalanceEntryForm(data)), request.user)
    return {"fluid_id": entry.id, "direction": entry.direction, "volume_ml": entry.volume_ml}


def handle_nursing_note(request, data):
    """Record a nurse's shift note — mirrors ``ipd.views.nursing_note_add``."""
    from ipd.forms import NursingNoteForm
    from ipd.models import Admission
    from ipd.services import record_nursing_note

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    note = record_nursing_note(admission,
                               _valid(NursingNoteForm(data, user=request.user)),
                               request.user)
    return {"note_id": note.id, "admission_id": admission.id}


def handle_care_task(request, data):
    """Log a routine care task — mirrors ``ipd.views.care_task_add``."""
    from ipd.forms import CareTaskForm
    from ipd.models import Admission
    from ipd.services import record_care_task

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    task = record_care_task(admission, _valid(CareTaskForm(data)), request.user)
    return {"task_id": task.id, "task": task.task}


def handle_handover(request, data):
    """Record an SBAR shift handover — mirrors ``ipd.views.handover_add``."""
    from ipd.forms import ShiftHandoverForm
    from ipd.models import Admission
    from ipd.services import record_handover

    _require(request.user, "ward")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    ho = record_handover(admission,
                         _valid(ShiftHandoverForm(data, user=request.user)),
                         request.user)
    return {"handover_id": ho.id, "admission_id": admission.id}


def handle_discharge(request, data):
    """Discharge and bill — mirrors ``ipd.views.admission_discharge``.

    A replay of an already-discharged admission is rejected permanently, so the
    stay can never be billed twice.
    """
    from ipd.forms import DischargeForm
    from ipd.models import Admission
    from ipd.services import discharge_patient

    _require(request.user, "ipd")
    admission = _parent(Admission, data.get("admission_id"), "admission")
    form = _valid(DischargeForm(data, instance=admission))
    res = discharge_patient(form.save(commit=False), request.user)
    return {"admission_id": res.admission.id,
            "invoice_id": res.invoice.id if res.invoice else None,
            "days": res.days, "bed_charges": str(res.bed_charges),
            "medicines_total": str(res.med_total)}


# --------------------------------------------------------------------------
# OT (theatre)
# --------------------------------------------------------------------------


def handle_surgery(request, data):
    """Schedule a surgery — mirrors ``ot.views.surgery_create`` (invoice included)."""
    from ot.forms import SurgeryRecordForm
    from ot.models import SurgeryRequest
    from ot.services import schedule_surgery

    _require(request.user, "ot")
    form = _valid(SurgeryRecordForm(data, user=request.user))
    surg_req = _parent(SurgeryRequest, data.get("request_id"), "surgery request",
                       required=False)
    record = schedule_surgery(
        form, request.user,
        surgery_request=surg_req if surg_req and surg_req.status == 'Pending' else None)
    return {"surgery_id": record.id, "patient": record.patient.full_name,
            "procedure": record.procedure.name}


def handle_surgery_advise(request, data):
    """A doctor advises surgery — mirrors ``ot.views.surgery_advise``."""
    from accounts.models import Notification
    from ot.models import SurgeryProcedure, SurgeryRequest
    from patients.models import Patient

    _require(request.user, "patients")
    patient = _parent(Patient, data.get("patient_id"), "patient")
    reason = _text(data, "reason")
    if not reason:
        raise ValidationError("Please enter the indication / reason for surgery.")
    urgency = data.get("urgency", "Elective")
    procedure = _parent(SurgeryProcedure, data.get("procedure"), "procedure",
                        required=False)
    req = SurgeryRequest.objects.create(
        patient=patient, advised_by=request.user, reason=reason,
        procedure=procedure, urgency=urgency)
    for role in ("RECEPTIONIST", "ADMIN"):
        Notification.send_to_role(
            hospital=patient.hospital, role=role,
            message=f"🔪 Surgery advised ({urgency}): {patient.full_name} — please schedule.",
            link='/ot/requests/')
    return {"request_id": req.id, "patient": patient.full_name}


def handle_surgery_category(request, data):
    """Add a surgery category — mirrors ``ot.views.category_create``."""
    from ot.forms import SurgeryCategoryForm
    _require(request.user, "ot")
    category = _valid(SurgeryCategoryForm(data)).save()
    return {"category_id": category.id, "name": category.name}


def handle_procedure(request, data):
    """Add a surgery procedure to the catalogue — mirrors
    ``ot.views.procedure_create``."""
    from ot.forms import SurgeryProcedureForm
    _require(request.user, "ot")
    procedure = _valid(SurgeryProcedureForm(data)).save()
    return {"procedure_id": procedure.id, "name": procedure.name}


# --------------------------------------------------------------------------
# billing
# --------------------------------------------------------------------------


def handle_expense(request, data):
    """Record an expense — mirrors ``billing.views.expense_create``."""
    from billing.forms import ExpenseForm
    _require(request.user, "expenses")
    expense = _valid(ExpenseForm(data)).save(commit=False)
    expense.recorded_by = request.user
    expense.save()
    return {"expense_id": expense.id, "amount": str(expense.amount)}


def handle_cash_closing(request, data):
    """Close the day's cash — mirrors ``billing.views.cash_closing_new``.

    The day's figures are recomputed from the server's own totals at replay
    time; only the counted cash and the note come from the device. A day that
    was already closed is rejected permanently rather than closed twice.
    """
    from billing.forms import CashClosingForm
    from billing.models import CashClosing
    from billing.services import cash_position

    _require(request.user, "cashclosing")
    form = _valid(CashClosingForm(data))
    closing = form.save(commit=False)
    if CashClosing.objects.filter(date=closing.date).exists():
        raise ValidationError(f"The books for {closing.date} are already closed.")
    pos = cash_position(closing.date)
    closing.cash_in = pos['cash_in']
    closing.cash_out = pos['cash_out']
    closing.expected = closing.opening + pos['net']
    closing.difference = closing.counted - closing.expected
    closing.closed_by = request.user
    closing.save()
    return {"closing_id": closing.id, "date": str(closing.date),
            "difference": str(closing.difference)}


# --------------------------------------------------------------------------
# Clinical add-ons — bedside / front-desk forms with no billing and no stock
# movement, so replay is a straight re-run of the same form the online view
# uses. Each stamps its actor from `request.user`, exactly as the view does.
# --------------------------------------------------------------------------


def handle_antenatal_visit(request, data):
    """Record an antenatal (ANC) visit — mirrors ``maternity.views.pregnancy_detail``.
    The pregnancy is the URL parent, so the form carries `pregnancy_id`."""
    from maternity.forms import AntenatalVisitForm
    from maternity.models import Pregnancy
    _require(request.user, "maternity")
    preg = _parent(Pregnancy, data.get("pregnancy_id"), "pregnancy")
    visit = _valid(AntenatalVisitForm(data, user=request.user)).save(commit=False)
    visit.pregnancy = preg
    visit.save()
    return {"visit_id": visit.id, "pregnancy_id": preg.pk}


def handle_vaccination(request, data):
    """Record a vaccine dose — mirrors ``vaccination.views.vaccination_list``. EPI
    camps and bedside dosing are exactly the no-signal case."""
    from vaccination.forms import VaccinationRecordForm
    _require(request.user, "vaccination")
    rec = _valid(VaccinationRecordForm(data, user=request.user)).save(commit=False)
    rec.created_by = request.user
    rec.save()
    return {"record_id": rec.id, "patient": rec.patient.full_name}


def handle_diagnosis(request, data):
    """Record a coded diagnosis — mirrors ``diagnosis.views.diagnosis_list``."""
    from diagnosis.forms import PatientDiagnosisForm
    _require(request.user, "diagnosis")
    dx = _valid(PatientDiagnosisForm(data, user=request.user)).save(commit=False)
    dx.created_by = request.user
    dx.save()
    return {"diagnosis_id": dx.id, "patient": dx.patient.full_name}


def handle_referral(request, data):
    """Record a referral — mirrors ``referral.views.referral_create``."""
    from referral.forms import ReferralForm
    _require(request.user, "referral")
    ref = _valid(ReferralForm(data, user=request.user)).save(commit=False)
    ref.created_by = request.user
    ref.save()
    return {"referral_id": ref.id, "patient": ref.patient.full_name}


def handle_consent(request, data):
    """Record a signed consent form — mirrors ``consent.views.consent_create``. The
    body is a frozen copy posted with the form, so nothing is referenced live."""
    from consent.forms import ConsentRecordForm
    _require(request.user, "consent")
    rec = _valid(ConsentRecordForm(data, user=request.user)).save(commit=False)
    rec.created_by = request.user
    rec.save()
    return {"consent_id": rec.id, "patient": rec.patient.full_name}


def handle_birth_certificate(request, data):
    """Issue a birth certificate — mirrors ``certificates.views.birth_create``.
    `serial_no` is allocated server-side in ``save()`` at replay, like MRN/invoice
    numbers — never guessed by the client."""
    from certificates.forms import BirthCertificateForm
    _require(request.user, "certificates")
    cert = _valid(BirthCertificateForm(data)).save(commit=False)
    cert.registered_by = request.user
    cert.save()
    return {"certificate_id": cert.id, "serial_no": cert.serial_no}


def handle_death_certificate(request, data):
    """Issue a death certificate — mirrors ``certificates.views.death_create``."""
    from certificates.forms import DeathCertificateForm
    _require(request.user, "certificates")
    cert = _valid(DeathCertificateForm(data, user=request.user)).save(commit=False)
    cert.registered_by = request.user
    cert.save()
    return {"certificate_id": cert.id, "serial_no": cert.serial_no}


# kind -> handler. A form is offline-capable only once its `kind` is here AND the
# template carries every id the handler needs (see the module docstring).
HANDLERS = {
    # reception / patients
    "visit": handle_visit,
    "patient": handle_patient,
    "patient_record": handle_patient_record,
    # OPD
    "appointment": handle_appointment,
    "department": handle_department,
    "doctor": handle_doctor,
    "payout": handle_payout,
    # prescriptions
    "prescription": handle_prescription,
    "rx_preset": handle_rx_preset,
    # pharmacy / inventory
    "sale": handle_sale,
    "medicine": handle_medicine,
    "adjustment": handle_adjustment,
    "purchase_return": handle_purchase_return,
    # suppliers & customers
    "supplier": handle_supplier,
    "supplier_payment": handle_supplier_payment,
    "customer": handle_customer,
    "customer_payment": handle_customer_payment,
    # lab & imaging
    "lab": handle_lab,
    "lab_result": handle_lab_result,
    "lab_test": handle_lab_test,
    "imaging": handle_imaging,
    "imaging_report": handle_imaging_report,
    "scan_type": handle_scan_type,
    # IPD
    "ward": handle_ward,
    "bed": handle_bed,
    "admission": handle_admission,
    "admission_advise": handle_admission_advise,
    "round": handle_round,
    "medication": handle_medication,
    "vital": handle_vital,
    "fluid": handle_fluid,
    "nursing_note": handle_nursing_note,
    "care_task": handle_care_task,
    "handover": handle_handover,
    "discharge": handle_discharge,
    # OT
    "surgery": handle_surgery,
    "surgery_advise": handle_surgery_advise,
    "surgery_category": handle_surgery_category,
    "procedure": handle_procedure,
    # billing
    "expense": handle_expense,
    "cash_closing": handle_cash_closing,
    # clinical add-ons (bedside / front-desk, no billing/stock)
    "antenatal_visit": handle_antenatal_visit,
    "vaccination": handle_vaccination,
    "diagnosis": handle_diagnosis,
    "referral": handle_referral,
    "consent": handle_consent,
    "birth_certificate": handle_birth_certificate,
    "death_certificate": handle_death_certificate,
}
