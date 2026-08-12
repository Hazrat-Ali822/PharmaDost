"""Imaging operations shared by the live view and offline replay — see
`lab/services.py` for why this indirection exists.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


def create_study(form, user):
    """Save a validated `ImagingStudyCreateForm` and raise the pending scan bill.

    Study and invoice commit together, so radiology never holds a scan that
    nobody was billed for.
    """
    from billing.services import create_service_invoice

    with transaction.atomic():
        study = form.save()
        invoice = create_service_invoice(
            patient=study.patient,
            items=[(f"{study.get_modality_display()}: {study.study_name}", study.price)],
            created_by=user, service='IMAGING')
        if invoice:
            study.invoice = invoice
            study.save()
    return study


def cancel_study(study, *, user, reason):
    """Cancel a scan the patient refused (or the doctor withdrew).

    A study *is* one scan, so there is no partial case as there is in the lab —
    cancelling it always takes its whole charge off the bill, which leaves the
    invoice empty and therefore VOID unless other services were billed on it.

    Refuses once a report exists: findings written means the scan was performed,
    and an already-done scan is a billing question, not a radiology one.
    """
    from billing.services import cancel_invoice_charge

    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("Please say why the scan is being cancelled.")
    if study.is_cancelled:
        raise ValidationError("This scan is already cancelled.")
    if study.is_reported:
        raise ValidationError(
            "A report has already been written for this study — the scan was done. "
            "If it must not be charged, void the bill from Billing instead.")

    with transaction.atomic():
        study.status = "Cancelled"
        study.cancelled_at = timezone.now()
        study.cancelled_by = user
        study.cancel_reason = reason
        study.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
        money = cancel_invoice_charge(
            study.invoice, f"{study.get_modality_display()}: {study.study_name}")

    # The referring doctor is waiting on this report; they have to be told it is
    # not coming. (One person's clinical fact, not an owner-level exception.)
    if study.referred_by and study.referred_by.is_active and user != study.referred_by:
        from accounts.models import Notification
        Notification.objects.create(
            user=study.referred_by,
            message=(f"🚫 Scan cancelled — {study.study_name} for "
                     f"{study.patient.full_name} (study #{study.id}): {reason}"),
            link=f"/imaging/studies/{study.id}/")
    return money
