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


# kind -> handler. New offline-capable actions (sale, vitals, orders) are added
# here as their phases land; the client sends the matching `kind`.
HANDLERS = {
    "visit": handle_visit,
}
