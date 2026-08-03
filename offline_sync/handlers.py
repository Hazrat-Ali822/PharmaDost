"""Apply a queued offline action by reusing the *exact* forms and services the
online views use.

The whole safety of the offline layer rests on this: an offline visit is bound to
the same `PatientForm` + `VisitForm`, validated by the same `clean()` methods and
booked through the same `_book_visit` as one typed live at the desk. There is no
second, looser code path that could let bad data in just because it arrived late.

Each handler is called inside a `transaction.atomic()` by the sync view and must
either return a JSON-serialisable result dict (echoed back to the client so it can
show the server-assigned MRN / token) or raise:

  * ``ValidationError``  -> a *permanent* rejection; the row is filed FAILED and
    surfaced to the desk to fix. Bad data will never validate on a retry.
  * any other exception  -> treated as *transient* by the view; nothing is
    recorded and the client retries the action next time it is online.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404


def _require(user, feature):
    """Offline replay must respect the same feature gates as the live view — a
    queued action is still that user acting, just deferred."""
    if not (user.is_superuser or user.has_feature(feature)):
        raise PermissionDenied(f"You do not have access to '{feature}'.")


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

    visit_form = VisitForm(data)
    patient_form = PatientForm(data) if is_new else None

    errors = {}
    if not visit_form.is_valid():
        errors.update(visit_form.errors)
    if patient_form is not None and not patient_form.is_valid():
        errors.update(patient_form.errors)
    if errors:
        raise ValidationError(errors)

    patient = patient_form.save() if is_new else get_object_or_404(Patient, pk=patient_id)
    appointment = _book_visit(request, patient, visit_form.cleaned_data)
    return {
        "patient_id": patient.pk,
        "mrn": patient.mrn,
        "patient_name": patient.full_name,
        "appointment_id": appointment.pk,
        "token_no": appointment.token_no,
        "doctor": appointment.doctor.full_name,
    }


def _one(value):
    """A form array key (medicine_id[]) arrives as a list when repeated, but as a
    bare value when a single row was submitted — normalise to a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def handle_sale(request, data):
    """Replay a pharmacy sale taken while offline — mirrors the POST parsing in
    ``sales.views.sale_create``, but with ``on_short="record"`` so a sale made
    against stock that has since run short is recorded and flagged for the
    pharmacist rather than refused (the medicine already left the shelf)."""
    from django.shortcuts import get_object_or_404
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
        try:
            q = int(qtys[i]) if i < len(qtys) and qtys[i] else 0
        except (TypeError, ValueError):
            q = 0
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

    customer = get_object_or_404(Customer, pk=data["customer_id"]) if data.get("customer_id") else None
    patient = get_object_or_404(Patient, pk=data["patient_id"]) if data.get("patient_id") else None

    sale = create_sale(
        items=items,
        sale_type=data.get("sale_type", Sale.RETAIL),
        customer=customer,
        patient=patient,
        customer_name=(data.get("customer_name") or "").strip(),
        discount=data.get("discount") or 0,
        paid=data.get("paid"),
        payment_method=data.get("payment_method", "CASH"),
        cashier=request.user,
        on_short="record",
    )

    if sale.needs_reconcile:
        # Exceptional — the pharmacist must verify the physical count. This is one
        # of the few things worth an active notification (see CLAUDE.md).
        Notification.send_to_role(
            sale.hospital, "PHARMACIST",
            f"Offline sale #{sale.id} dispensed stock that was short — please "
            f"reconcile: {sale.reconcile_note}", f"/sales/{sale.id}/")

    return {
        "sale_id": sale.id,
        "total": str(sale.total),
        "needs_reconcile": sale.needs_reconcile,
        "reconcile_note": sale.reconcile_note,
    }


def handle_patient(request, data):
    """Register a new patient while offline — mirrors patients.views.patient_create."""
    from patients.forms import PatientForm
    _require(request.user, "patients")
    form = PatientForm(data)
    if not form.is_valid():
        raise ValidationError(form.errors)
    patient = form.save()
    return {
        "patient_id": patient.pk,
        "mrn": patient.mrn,
        "full_name": patient.full_name,
    }


def handle_lab(request, data):
    """Replay an offline lab order creation."""
    from lab.forms import LabOrderForm
    from lab.views import _create_order
    from patients.models import Patient
    _require(request.user, "lab")
    form = LabOrderForm(data)
    if not form.is_valid():
        raise ValidationError(form.errors)
    patient_id = data.get("patient_id")
    patient = get_object_or_404(Patient, pk=patient_id) if patient_id else None
    order = _create_order(request, form.cleaned_data, patient=patient)
    return {
        "order_id": order.id,
        "test_name": order.test_catalog.name if order.test_catalog else "",
    }


# kind -> handler. New offline-capable actions are added here as their phases
# land; the client sends the matching `kind`.
HANDLERS = {
    "visit": handle_visit,
    "sale": handle_sale,
    "patient": handle_patient,
    "lab": handle_lab,
}

