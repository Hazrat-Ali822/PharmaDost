"""Ward operations shared by the live views and offline replay.

Admitting, logging a dose and discharging all move money and stock, so each one
lives here exactly once. `ipd.views` and `offline_sync.handlers` call the same
function — a dose recorded at the bedside with no signal is billed by the same
rules as one recorded online.

Each function returns a small result object rather than calling `messages.*`,
because the offline replay has no request to attach messages to; the live view
turns the result into the wording the staff see.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Admission, Bed


class AdmissionResult:
    def __init__(self, admission, bed):
        self.admission = admission
        self.bed = bed


class MedicationResult:
    """What actually happened to stock, so the caller can warn appropriately."""

    def __init__(self, log, stock_short=None, warnings=()):
        self.log = log
        self.stock_short = stock_short      # str reason, or None
        self.warnings = list(warnings)      # allergy / duplicate-salt advisories


class DischargeResult:
    def __init__(self, admission, invoice, days, bed_charges, med_total):
        self.admission = admission
        self.invoice = invoice
        self.days = days
        self.bed_charges = bed_charges
        self.med_total = med_total


def admit_patient(admission, user=None, adm_request=None):
    """Put `admission` (an unsaved `Admission`) into its bed.

    The bed row is locked and re-checked so two receptionists cannot admit two
    patients into the same bed, and a patient cannot hold two beds at once.
    Raises `ValidationError` when either check fails — that is a permanent
    rejection offline, not something a retry can fix.
    """
    with transaction.atomic():
        try:
            bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
        except Bed.DoesNotExist:
            raise ValidationError("Selected bed was not found.")
        if bed.status != 'Available':
            raise ValidationError(f"Bed {bed.bed_number} is no longer available.")
        if Admission.objects.filter(patient=admission.patient, status='Admitted').exists():
            raise ValidationError(
                f"{admission.patient.full_name} already has an active admission.")
        bed.status = 'Occupied'
        bed.save(update_fields=['status'])
        admission.save()
        if adm_request:
            adm_request.status = 'Admitted'
            adm_request.admission = admission
            adm_request.save(update_fields=['status', 'admission'])
    return AdmissionResult(admission, bed)


def log_medication(admission, log, medicine, user):
    """Record one administered dose — see CLAUDE.md for why this never refuses.

    `log` is the unsaved `MedicationLog` from a validated form. Three cases record
    with no stock movement and no charge: no catalogue medicine, the patient's own
    supply, or pharmacy stock that has run short. The dose is already inside the
    patient; refusing to save would leave the chart lying about what was given.
    """
    from inventory.models import Medicine
    from inventory.safety import screen_medicines

    log.admission = admission
    log.administered_by = user
    log.medicine = medicine
    if medicine is not None and not log.medicine_name:
        log.medicine_name = (f"{medicine.name} ({medicine.brand})"
                             if medicine.brand else medicine.name)

    stock_short = None
    if medicine is None or log.source != 'PHARMACY':
        log.unit_price = Decimal('0.00')
        log.save()
    else:
        # Stock and money move together, on a locked row (see CLAUDE.md).
        try:
            with transaction.atomic():
                med = Medicine.objects.select_for_update().get(pk=medicine.pk)
                med.reduce_stock(log.quantity)
                # Freeze the price of the day — the catalogue may change before
                # this patient is discharged and billed.
                log.unit_price = med.price or Decimal('0.00')
                log.save()
        except ValueError as exc:
            stock_short = str(exc)
            log.unit_price = Decimal('0.00')
            marker = ' [not deducted — pharmacy stock short]'
            log.notes = (log.notes + marker)[:255] if log.notes else marker.strip()
            log.save()

    # Allergy check happens AFTER recording: the dose is already given, so the
    # chart must reflect reality — the warning is for the staff to act on.
    warnings = screen_medicines(admission.patient, [medicine]) if medicine is not None else []
    return MedicationResult(log, stock_short, warnings)


def discharge_patient(admission, user):
    """Free the bed and raise the discharge bill — bed charges plus every dose.

    `admission` must already carry the discharge notes from the form. Raises
    `ValidationError` if the patient is already out, so a replayed offline
    discharge is rejected permanently instead of billing the stay twice.
    """
    from billing.services import create_service_invoice

    if admission.status == 'Discharged':
        raise ValidationError("Patient is already discharged.")

    with transaction.atomic():
        admission.status = 'Discharged'
        admission.discharge_date = timezone.now()
        admission.save()

        # Free the bed (locked so a concurrent admission can't clobber it)
        bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
        bed.status = 'Available'
        bed.save(update_fields=['status'])

        # Bed charges = calendar days the bed was occupied, counting the admission
        # day and the discharge day (inclusive), minimum one day — this is how
        # hospital room bills are normally itemised.
        days = (admission.discharge_date.date() - admission.admission_date.date()).days + 1
        if days < 1:
            days = 1
        bed_charges = days * admission.bed.ward.daily_rate

        items = [(f"IPD Bed Charges: Bed {admission.bed.bed_number} "
                  f"({admission.bed.ward.name}) — {days} Day(s)", bed_charges)]

        # Everything the ward gave this patient from pharmacy stock. Without this
        # the discharge bill was bed charges only, so every dose administered
        # during the stay was given away free.
        med_total = Decimal('0.00')
        for log in admission.medication_logs.select_related('medicine').all():
            if log.charge:
                items.append((f"Medicine: {log.medicine_name} x{log.quantity}", log.charge))
                med_total += log.charge

        invoice = create_service_invoice(
            patient=admission.patient, items=items, created_by=user, paid=0)
        if invoice:
            admission.discharge_invoice = invoice
            admission.save(update_fields=['discharge_invoice'])

    return DischargeResult(admission, invoice, days, bed_charges, med_total)


def record_vitals(admission, form, user):
    """Save a bound VitalsObservationForm as a nursing observation. Same path for
    the online view and offline replay; returns the saved VitalsObservation (whose
    `.mews` the caller reads to decide the escalation message)."""
    obs = form.save(commit=False)
    obs.admission = admission
    obs.taken_by = user
    obs.save()
    return obs


def record_fluid(admission, form, user):
    """Save a bound FluidBalanceEntryForm as one intake/output line."""
    entry = form.save(commit=False)
    entry.admission = admission
    entry.recorded_by = user
    entry.save()
    return entry


def record_nursing_note(admission, form, user):
    note = form.save(commit=False)
    note.admission = admission
    note.noted_by = user
    note.save()
    return note


def record_handover(admission, form, user):
    ho = form.save(commit=False)
    ho.admission = admission
    ho.from_nurse = user
    ho.save()
    return ho


def record_care_task(admission, form, user):
    task = form.save(commit=False)
    task.admission = admission
    task.done_by = user
    task.save()
    return task
