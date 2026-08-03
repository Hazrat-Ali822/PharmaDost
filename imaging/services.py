"""Imaging operations shared by the live view and offline replay — see
`lab/services.py` for why this indirection exists.
"""
from django.db import transaction


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
            created_by=user)
        if invoice:
            study.invoice = invoice
            study.save()
    return study
