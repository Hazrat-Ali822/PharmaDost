"""The one path a trip takes, so the online view and any replay agree.

Kept in a service module for the same reason `lab/`, `imaging/`, `ipd/` and
`ot/` have theirs: the view and the offline handler must not grow two slightly
different versions of "book a trip".
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Ambulance, AmbulanceTrip


def apply_vehicle_rates(trip, ambulance):
    """Freeze the vehicle's rates onto the trip, unless the operator typed one.

    A long or awkward run can be charged more than the standard rate, so a value
    already on the trip wins; only the untouched (zero) ones are filled in.
    """
    for field in ('base_charge', 'per_km_charge', 'waiting_charge_per_hour', 'cost_price'):
        if not getattr(trip, field):
            setattr(trip, field, getattr(ambulance, field) or Decimal('0.00'))
    return trip


@transaction.atomic
def dispatch_trip(trip, *, user=None):
    """Book a trip and take the vehicle out of the free list.

    The vehicle is locked and re-checked inside the transaction: two operators
    with the board open would otherwise both send the same ambulance, and unlike
    a double-booked bed there is no second one parked outside.
    """
    ambulance = Ambulance.all_objects.select_for_update().get(pk=trip.ambulance_id)
    if not ambulance.is_active:
        raise ValidationError('That ambulance is not in service.')
    if ambulance.status == Ambulance.STATUS_ON_TRIP:
        raise ValidationError(f'{ambulance} is already out on a trip.')

    apply_vehicle_rates(trip, ambulance)
    if trip.driver_id is None:
        trip.driver_id = ambulance.driver_id
    if user is not None and trip.created_by_id is None:
        trip.created_by = user
    trip.save()

    ambulance.status = Ambulance.STATUS_ON_TRIP
    ambulance.save(update_fields=['status'])
    return trip


@transaction.atomic
def complete_trip(trip, *, user=None, bill=True):
    """Finish a trip, free the vehicle, and raise the bill if there is one.

    Returns the invoice, or None — a zero-rate trip (a hospital that does not
    charge, or a transfer it caused itself) produces no lines and therefore no
    invoice, which is correct rather than a failure.
    """
    from django.utils import timezone

    if trip.status == AmbulanceTrip.STATUS_COMPLETED:
        raise ValidationError('That trip is already completed.')

    trip.status = AmbulanceTrip.STATUS_COMPLETED
    trip.completed_at = trip.completed_at or timezone.now()

    invoice = None
    if bill and trip.patient_id and trip.invoice_id is None:
        # Only a registered patient can be invoiced — an invoice needs one. A
        # trip for a caller with no patient record is recorded and charged in
        # cash at the desk; forcing a Patient row for a body transfer would put
        # the deceased in the patient register.
        from billing.services import create_service_invoice
        lines = trip.charge_lines()
        if lines:
            invoice = create_service_invoice(
                patient=trip.patient, items=lines, created_by=user,
                service='AMBULANCE')
            trip.invoice = invoice

    trip.save()
    _free_vehicle(trip.ambulance_id)
    return invoice


@transaction.atomic
def cancel_trip(trip, *, reason, user=None):
    """Withdraw a trip. The reason is mandatory, as everywhere else a service is
    cancelled — "why did the ambulance not go" has to stay answerable."""
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Say why the trip was cancelled.')
    if trip.status == AmbulanceTrip.STATUS_COMPLETED:
        raise ValidationError('A completed trip cannot be cancelled — the run happened.')
    trip.status = AmbulanceTrip.STATUS_CANCELLED
    trip.cancel_reason = reason
    trip.save(update_fields=['status', 'cancel_reason'])
    _free_vehicle(trip.ambulance_id)
    return trip


def _free_vehicle(ambulance_id):
    """Put a vehicle back on the board once nothing is holding it.

    Keyed on "has no open trip" rather than on this one trip finishing, because a
    vehicle sent out twice by mistake must not come back free while the second
    run is still going. Never touches one that is out of service.
    """
    ambulance = Ambulance.all_objects.select_for_update().get(pk=ambulance_id)
    if ambulance.status == Ambulance.STATUS_MAINTENANCE:
        return
    still_out = AmbulanceTrip.all_objects.filter(
        ambulance_id=ambulance_id, status__in=AmbulanceTrip.OPEN_STATUSES).exists()
    new_status = Ambulance.STATUS_ON_TRIP if still_out else Ambulance.STATUS_AVAILABLE
    if ambulance.status != new_status:
        ambulance.status = new_status
        ambulance.save(update_fields=['status'])
