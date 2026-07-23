"""Fetching doctors together with whether they are sitting right now.

`Doctor.availability()` reads `schedules` and `availability_overrides` through
`.all()`, so it costs two queries per doctor unless they are prefetched. The
reception screen lists every doctor on every load, so it always goes through
here — a plain `Doctor.objects.filter(...)` there is a 20-query page.
"""
from django.db.models import Prefetch
from django.utils import timezone

from .models import Doctor, DoctorAvailabilityOverride


def doctors_with_availability(hospital=None, department=None, on=None):
    """Active doctors, ready for `.availability()` without further queries.

    Only TODAY's overrides are prefetched — the table grows by a row per doctor
    per leave day, and the screen never looks at any other date.
    """
    today = (on or timezone.localtime()).date()
    qs = (Doctor.objects.filter(is_active=True)
          .select_related('department')
          .prefetch_related(
              'schedules',
              Prefetch('availability_overrides',
                       queryset=DoctorAvailabilityOverride.objects.filter(date=today)),
          )
          .order_by('department__name', 'full_name'))
    if hospital is not None:
        # Doctor has no hospital column of its own — it is scoped through the
        # linked user account. Roster rows with no user belong to nobody in
        # particular and stay visible (see CLAUDE.md).
        from django.db.models import Q
        qs = qs.filter(Q(user__hospital=hospital) | Q(user__isnull=True))
    if department is not None:
        qs = qs.filter(department=department)
    return qs


def split_by_availability(doctors, on=None):
    """(sitting_now, not_sitting) — the reception screen shows the first list and
    keeps the second behind a toggle, because an emergency still has to be
    bookable against a doctor who is off."""
    at = on or timezone.localtime()
    sitting, away = [], []
    for doctor in doctors:
        state = doctor.availability(at)
        (sitting if state['available'] else away).append((doctor, state))
    return sitting, away
