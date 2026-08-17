"""Theatre operations shared by the live view and offline replay."""
from decimal import Decimal

from django.db import transaction


def apply_procedure_defaults(record):
    """Fill any charge left at zero from the procedure's catalogue rate.

    Prefill rather than overwrite: the scheduling form lets the theatre type a
    different figure for a long or complicated operation, and that typed figure
    must survive. A genuinely free part stays free because the form posts 0 and
    the catalogue rate is 0 too — the only case this cannot express is "the
    catalogue charges for it but this one operation does not", which the desk
    handles by discounting the invoice.
    """
    procedure = record.procedure
    if procedure is None:
        return record
    pairs = (('surgeon_charge', 'standard_charge'),
             ('ot_charge', 'ot_charge'),
             ('anesthesia_charge', 'anesthesia_charge'),
             ('consumables_charge', 'consumables_charge'),
             ('cost_price', 'cost_price'))
    for own, catalogue in pairs:
        if not getattr(record, own):
            setattr(record, own, getattr(procedure, catalogue) or Decimal('0.00'))
    return record


def schedule_surgery(form, user, surgery_request=None):
    """Save a validated `SurgeryRecordForm` and raise its invoice.

    Record and invoice commit together — never leave a surgery saved but
    unbilled, or vice-versa. Closes the originating doctor's advice, if any.

    The bill is **itemised**: surgeon's fee, theatre charge, anaesthesia and
    consumables each get their own line (a zero part is simply omitted), so the
    patient can read what they are paying for and the hospital can see which
    part of an operation earns. It used to be one opaque `standard_charge`.
    """
    from billing.services import create_service_invoice

    with transaction.atomic():
        record = form.save(commit=False)
        apply_procedure_defaults(record)
        record.save()
        form.save_m2m()

        items = record.charge_lines()
        if items:
            invoice = create_service_invoice(
                patient=record.patient,
                items=items,
                created_by=user,
                paid=0,
                service='PROCEDURE',
            )
            if invoice:
                record.invoice = invoice
                record.save(update_fields=['invoice'])
        if surgery_request:
            surgery_request.status = 'Scheduled'
            surgery_request.surgery = record
            surgery_request.save(update_fields=['status', 'surgery'])
    return record
