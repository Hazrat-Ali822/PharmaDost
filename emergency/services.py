"""Emergency intake — one call registers the casualty visit and, if a fee is
given, raises its consultation invoice (offline replay can reuse this)."""
from decimal import Decimal

from django.db import transaction

from .models import EmergencyCase


@transaction.atomic
def register_case(*, patient, created_by, triage='YELLOW', chief_complaint='',
                  mode_of_arrival='WALKIN', brought_by='', is_mlc=False, mlc_no='',
                  pulse='', bp='', temp='', spo2='', attending_doctor=None,
                  consultation_fee=None):
    case = EmergencyCase.objects.create(
        patient=patient, created_by=created_by, triage=triage,
        chief_complaint=chief_complaint, mode_of_arrival=mode_of_arrival,
        brought_by=brought_by, is_mlc=is_mlc, mlc_no=mlc_no,
        pulse=pulse, bp=bp, temp=temp, spo2=spo2, attending_doctor=attending_doctor,
    )
    if consultation_fee:
        from billing.services import create_service_invoice
        invoice = create_service_invoice(
            patient=patient,
            items=[('Emergency Consultation', Decimal(str(consultation_fee)))],
            created_by=created_by,
        )
        if invoice:
            case.invoice = invoice
            case.save(update_fields=['invoice'])
    return case
