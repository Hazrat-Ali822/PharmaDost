"""Ambulance dispatch board, fleet and drivers.

Scoping note: `Ambulance`, `AmbulanceDriver` and `AmbulanceTrip` all carry a
`hospital` FK **and** a `TenantManager`, so `objects` is already scoped and
already fail-closed. The config screens still go through `all_objects` keyed on
the hospital *value*, for the reason the lab/imaging catalogue editors do —
`TenantManager` lets a superuser past unfiltered, and these screens write.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required, role_required

from .forms import AmbulanceDriverForm, AmbulanceForm, AmbulanceTripForm
from .models import Ambulance, AmbulanceDriver, AmbulanceTrip
from .services import cancel_trip, complete_trip, dispatch_trip


def _hospital(request):
    return None if request.user.is_superuser else request.user.hospital


def _fleet(request):
    return Ambulance.all_objects.filter(hospital=_hospital(request))


def _drivers(request):
    return AmbulanceDriver.all_objects.filter(hospital=_hospital(request))


# ------------------------------------------------------------------- board

@feature_required('ambulance')
def dispatch_board(request):
    """Who is out, who is free, and today's runs — the operator's one screen."""
    fleet = list(_fleet(request).filter(is_active=True).select_related('driver')
                 .annotate(open_trips=Count('trips', filter=Q(
                     trips__status__in=AmbulanceTrip.OPEN_STATUSES))))
    open_trips = (AmbulanceTrip.objects.filter(status__in=AmbulanceTrip.OPEN_STATUSES)
                  .select_related('ambulance', 'driver', 'patient'))
    today = timezone.localdate()
    todays = (AmbulanceTrip.objects.filter(called_at__date=today)
              .select_related('ambulance', 'driver', 'patient'))

    return render(request, 'ambulance/dispatch_board.html', {
        'fleet': fleet,
        'free': [a for a in fleet if a.is_free],
        'open_trips': open_trips,
        'todays': todays,
        'today': today,
    })


# ------------------------------------------------------------------- trips

@feature_required('ambulance')
def trip_list(request):
    trips = AmbulanceTrip.objects.select_related('ambulance', 'driver', 'patient')
    status = request.GET.get('status')
    if status in dict(AmbulanceTrip.STATUS_CHOICES):
        trips = trips.filter(status=status)
    q = (request.GET.get('q') or '').strip()
    if q:
        trips = trips.filter(
            Q(patient__full_name__icontains=q) | Q(contact_name__icontains=q)
            | Q(contact_phone__icontains=q) | Q(from_location__icontains=q)
            | Q(to_location__icontains=q) | Q(ambulance__registration_no__icontains=q))

    from pharma_mgmt.pagination import paginate
    page = paginate(request, trips)
    return render(request, 'ambulance/trip_list.html', {
        'trips': page, 'page_obj': page, 'q': q, 'status': status,
        'status_choices': AmbulanceTrip.STATUS_CHOICES,
    })


@feature_required('ambulance')
def trip_create(request):
    if request.method == 'POST':
        form = AmbulanceTripForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                trip = dispatch_trip(form.save(commit=False), user=request.user)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
            else:
                messages.success(request, f'{trip.ambulance} dispatched.')
                return redirect('ambulance:trip_detail', pk=trip.pk)
    else:
        form = AmbulanceTripForm(user=request.user,
                                 initial={'called_at': timezone.now()})
    return render(request, 'ambulance/trip_form.html', {
        'form': form,
        'free': _fleet(request).filter(is_active=True, status=Ambulance.STATUS_AVAILABLE),
    })


@feature_required('ambulance')
def trip_detail(request, pk):
    trip = get_object_or_404(
        AmbulanceTrip.objects.select_related('ambulance', 'driver', 'patient', 'invoice'),
        pk=pk)
    return render(request, 'ambulance/trip_detail.html', {'trip': trip})


@feature_required('ambulance')
def trip_complete(request, pk):
    trip = get_object_or_404(AmbulanceTrip.objects, pk=pk)
    if request.method == 'POST':
        # Distance and waiting are usually only known at the end of the run.
        for field in ('distance_km', 'waiting_hours'):
            raw = request.POST.get(field)
            if raw not in (None, ''):
                try:
                    setattr(trip, field, raw)
                except (TypeError, ValueError):
                    pass
        try:
            invoice = complete_trip(trip, user=request.user)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            if invoice:
                from user_mgmt.models import current_currency
                messages.success(
                    request,
                    f'Trip completed. Bill raised: {current_currency()} {invoice.total}.')
            else:
                messages.success(request, 'Trip completed.')
    return redirect('ambulance:trip_detail', pk=trip.pk)


@feature_required('ambulance')
def trip_cancel(request, pk):
    trip = get_object_or_404(AmbulanceTrip.objects, pk=pk)
    if request.method == 'POST':
        try:
            cancel_trip(trip, reason=request.POST.get('reason'), user=request.user)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            messages.success(request, 'Trip cancelled.')
    return redirect('ambulance:trip_detail', pk=trip.pk)


# ------------------------------------------------------------ fleet config

@role_required(['ADMIN'])
@feature_required('ambulance')
def fleet_list(request):
    return render(request, 'ambulance/fleet_list.html', {
        'fleet': _fleet(request).select_related('driver'),
        'drivers': _drivers(request),
    })


@role_required(['ADMIN'])
@feature_required('ambulance')
def ambulance_form(request, pk=None):
    obj = get_object_or_404(_fleet(request), pk=pk) if pk else None
    if request.method == 'POST':
        form = AmbulanceForm(request.POST, instance=obj, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Ambulance saved.')
            return redirect('ambulance:fleet_list')
    else:
        form = AmbulanceForm(instance=obj, user=request.user)
    return render(request, 'ambulance/ambulance_form.html', {'form': form, 'obj': obj})


@role_required(['ADMIN'])
@feature_required('ambulance')
def driver_form(request, pk=None):
    obj = get_object_or_404(_drivers(request), pk=pk) if pk else None
    if request.method == 'POST':
        form = AmbulanceDriverForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Driver saved.')
            return redirect('ambulance:fleet_list')
    else:
        form = AmbulanceDriverForm(instance=obj)
    return render(request, 'ambulance/driver_form.html', {'form': form, 'obj': obj})
