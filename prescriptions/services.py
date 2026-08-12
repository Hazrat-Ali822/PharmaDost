"""Writing a prescription — shared by the live view and offline replay.

A prescription is never just its header: the medicines, the ticked lab tests and
the ticked scans all commit with it, and the appointment is closed. Offline
replay calls this same function, so an Rx written with no signal reaches the
pharmacy queue with its items intact rather than as an empty shell.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


class PrescriptionResult:
    def __init__(self, prescription, medicines, n_tests, n_scans, warnings):
        self.prescription = prescription
        self.medicines = medicines
        self.n_tests = n_tests
        self.n_scans = n_scans
        self.warnings = warnings


def save_prescription(appointment, form, med_formset, user):
    """Commit a validated `PrescriptionForm` + item formset against `appointment`."""
    from inventory.safety import screen_medicines
    from prescriptions.views import _order_lab_tests, _order_scans

    patient = appointment.patient
    with transaction.atomic():
        prescription = form.save(commit=False)
        prescription.appointment = appointment
        prescription.save()

        med_formset.instance = prescription
        medicines = med_formset.save()  # blank extra rows are skipped automatically

        warnings = screen_medicines(patient, [pi.medicine for pi in medicines])

        n_tests = _order_lab_tests(patient, list(form.cleaned_data.get('tests') or []), user)
        n_scans = _order_scans(list(form.cleaned_data.get('scans') or []), patient, user)

        appointment.status = 'DONE'
        appointment.save()

    return PrescriptionResult(prescription, medicines, n_tests, n_scans, warnings)


def _notify_prescriber(prescription, message):
    """Tell the doctor who wrote the Rx that the patient declined something on it.

    Straight to that one person, not `notify_admins`: the prescriber is the only
    one who needs to know, and it changes what they expect at the follow-up.
    """
    from accounts.models import Notification

    doctor = getattr(prescription.appointment.doctor, 'user', None)
    if doctor and doctor.is_active:
        Notification.objects.create(
            user=doctor, message=message, link=f"/prescriptions/{prescription.pk}/")


def _sync_status(prescription):
    """A prescription with nothing left to dispense is finished, not pending.

    Without this the Rx sits in the pharmacy's PENDING queue for ever after the
    patient has walked out having refused every medicine on it.
    """
    if prescription.status in ('DISPENSED', 'CANCELLED'):
        return
    if not prescription.items.filter(is_cancelled=False).exists():
        prescription.status = 'CANCELLED'
        prescription.save(update_fields=['status'])


def cancel_item(item, *, user, reason):
    """Mark one prescribed medicine as declined.

    No money moves: unlike a lab test or a scan, a medicine is billed when it is
    **dispensed** at the POS, not when it is prescribed — so there is no invoice
    line to take off. This exists so the pharmacy queue and the printed Rx tell
    the truth about what the patient actually took.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("Please say why the medicine is being cancelled.")
    if item.is_cancelled:
        raise ValidationError("That medicine is already cancelled.")

    prescription = item.prescription
    if prescription.is_cancelled:
        raise ValidationError("This whole prescription is already cancelled.")

    with transaction.atomic():
        item.is_cancelled = True
        item.cancelled_at = timezone.now()
        item.cancelled_by = user
        item.cancel_reason = reason
        item.save(update_fields=['is_cancelled', 'cancelled_at',
                                 'cancelled_by', 'cancel_reason'])
        _sync_status(prescription)

    _notify_prescriber(
        prescription,
        f"🚫 Medicine declined — {item.display_name} on Rx #{prescription.pk} for "
        f"{prescription.appointment.patient.full_name}: {reason}")


def cancel_prescription(prescription, *, user, reason):
    """Cancel every medicine still outstanding on a prescription.

    Already-dispensed medicines are not touched — they were sold, the patient has
    them, and the sale is its own record. A fully DISPENSED Rx therefore cannot be
    cancelled at all.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("Please say why the prescription is being cancelled.")
    if prescription.is_cancelled:
        raise ValidationError("This prescription is already cancelled.")
    if prescription.status == 'DISPENSED':
        raise ValidationError(
            "Every medicine on this prescription has already been dispensed — the "
            "patient has them. Use a sale return in the pharmacy instead.")

    with transaction.atomic():
        live = list(prescription.items.filter(is_cancelled=False))
        for item in live:
            item.is_cancelled = True
            item.cancelled_at = timezone.now()
            item.cancelled_by = user
            item.cancel_reason = reason
            item.save(update_fields=['is_cancelled', 'cancelled_at',
                                     'cancelled_by', 'cancel_reason'])
        prescription.status = 'CANCELLED'
        prescription.cancelled_at = timezone.now()
        prescription.cancelled_by = user
        prescription.cancel_reason = reason
        prescription.save(update_fields=['status', 'cancelled_at',
                                         'cancelled_by', 'cancel_reason'])

    _notify_prescriber(
        prescription,
        f"🚫 Prescription #{prescription.pk} cancelled for "
        f"{prescription.appointment.patient.full_name} "
        f"({len(live)} medicine(s)): {reason}")
    return len(live)
