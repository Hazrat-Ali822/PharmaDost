"""Theatre operations shared by the live view and offline replay."""
from django.db import transaction


def schedule_surgery(form, user, surgery_request=None):
    """Save a validated `SurgeryRecordForm` and raise its invoice.

    Record and invoice commit together — never leave a surgery saved but
    unbilled, or vice-versa. Closes the originating doctor's advice, if any.
    """
    from billing.services import create_service_invoice

    with transaction.atomic():
        record = form.save()
        create_service_invoice(
            patient=record.patient,
            items=[(f"OT Surgery: {record.procedure.name} "
                    f"(Surgeon: Dr. {record.lead_surgeon.full_name})",
                    record.procedure.standard_charge)],
            created_by=user,
            paid=0,
        )
        if surgery_request:
            surgery_request.status = 'Scheduled'
            surgery_request.surgery = record
            surgery_request.save(update_fields=['status', 'surgery'])
    return record
