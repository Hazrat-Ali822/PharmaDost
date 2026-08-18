"""Turn punches into attendance days.

A fingerprint terminal reports *events*: enrolment number 7 touched the reader
at 08:52, and again at 21:03. Payroll needs a *verdict*: on this date this
person was present, half a day, on leave, or absent — and that verdict is what
`hr.views.attendance_summary` counts and `salary_create` deducts from.

Getting the machine talking is the easy half. This is the half where a mistake
takes money off somebody's salary, so the rules are conservative in one
direction on purpose: **when the data does not say a person was absent, this
does not say it either.** Four of them matter.

1. **A day with no punches from anyone is never marked absent.** That is what a
   switched-off machine, a public holiday and a Sunday all look like from here,
   and they are indistinguishable. A naive import marks the entire staff absent
   for the three days the device was unplugged and quietly cuts everybody's pay.
   Those days are reported back as `no_data` for a human to classify.

2. **A missing punch-out is not an absence.** One punch means present with the
   out-time unknown. Somebody who forgets to touch the reader on the way home
   has still worked the day.

3. **A hand-entered row always wins.** `Attendance.source` records who decided a
   day, and a rebuild skips anything marked MANUAL. The reader misses fingers,
   people punch on a colleague's behalf, the device clock drifts — and a
   correction that a later import silently reverses is worse than no import.

4. **An unmapped enrolment number is never discarded.** The punch is stored with
   `user = None` and reported; once the mapping is added, a rebuild picks it up.
   Deleting it would delete the only evidence that somebody was at work.

Everything here is recomputable. Punches are the record; attendance rows are a
derived view of them, so a corrected mapping or a changed threshold can simply
be rebuilt rather than re-collected from the machine.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# Below this many hours between the first and last punch, the day is a half day.
# A module constant rather than a setting because no hospital has asked for a
# different number yet, and a configuration knob nobody turns is still a knob
# everybody has to understand. Making it per-hospital is a `SiteSettings` field
# and one line here, the day somebody wants 5.
HALF_DAY_HOURS = 4.0

# A second touch this soon after the last one is the same person pressing twice
# because the first beep was not convincing. Counting it as a separate event
# does no harm to first/last, but it makes the punch list unreadable.
DEDUPE_SECONDS = 60


def resolve_user(hospital, device_user_id):
    """Which staff member is enrolment number `device_user_id`?"""
    from .models import StaffProfile
    if not device_user_id:
        return None
    profile = (StaffProfile.all_objects
               .filter(hospital=hospital, biometric_id=str(device_user_id).strip())
               .select_related('user').first())
    return profile.user if profile else None


def _leave_dates(hospital, user_ids, start, end):
    """Approved leave, as a set of (user_id, date) — so an absence that was
    already agreed is not reported as one."""
    from .models import LeaveRequest
    out = set()
    rows = (LeaveRequest.all_objects
            .filter(user_id__in=user_ids, status='APPROVED',
                    start_date__lte=end, end_date__gte=start)
            .values_list('user_id', 'start_date', 'end_date'))
    for uid, s, e in rows:
        day = max(s, start)
        last = min(e, end)
        while day <= last:
            out.add((uid, day))
            day += timedelta(days=1)
    return out


def rebuild_attendance(hospital, start, end, mark_absent=True):
    """Recompute attendance from punches for `start`..`end` inclusive.

    Returns a report dict rather than writing silently — the import screen shows
    it and asks before committing anything, because this writes the table
    payroll reads.
    """
    from django.db import transaction
    from .models import Attendance, BiometricPunch, StaffProfile

    today = timezone.localdate()
    if end > today:
        end = today                      # tomorrow is not an absence

    report = {'present': 0, 'half': 0, 'absent': 0, 'leave': 0,
              'skipped_manual': 0, 'no_data_days': [], 'unmapped': {},
              'start': start, 'end': end}
    if start > end:
        return report

    punches = (BiometricPunch.all_objects
               .filter(hospital=hospital,
                       punched_at__date__gte=start, punched_at__date__lte=end)
               .order_by('punched_at'))

    # (user_id, date) -> [datetime, ...]; and the days the machine was alive at all
    by_person_day = defaultdict(list)
    active_days = set()
    for p in punches:
        local = timezone.localtime(p.punched_at)
        day = local.date()
        active_days.add(day)
        if p.user_id:
            by_person_day[(p.user_id, day)].append(local)
        else:
            report['unmapped'][p.device_user_id] = \
                report['unmapped'].get(p.device_user_id, 0) + 1

    staff = list(StaffProfile.all_objects.filter(hospital=hospital)
                 .values_list('user_id', flat=True))
    on_leave = _leave_dates(hospital, staff, start, end)

    # Days inside the range that nobody touched the machine on. Reported, never
    # written — see rule 1.
    day = start
    while day <= end:
        if day not in active_days:
            report['no_data_days'].append(day)
        day += timedelta(days=1)

    existing = {(a.user_id, a.date): a for a in Attendance.all_objects.filter(
        hospital=hospital, date__gte=start, date__lte=end)}

    to_write = []
    for uid in staff:
        day = start
        while day <= end:
            if day not in active_days:
                day += timedelta(days=1)
                continue                          # machine was off; say nothing

            row = existing.get((uid, day))
            if row is not None and row.source == Attendance.SOURCE_MANUAL:
                report['skipped_manual'] += 1
                day += timedelta(days=1)
                continue                          # rule 3: a person decided this

            times = sorted(by_person_day.get((uid, day), []))
            if times:
                first, last = times[0], times[-1]
                hours = (last - first).total_seconds() / 3600.0
                # rule 2: one punch is present, not absent
                status = 'HALF' if (len(times) > 1 and hours < HALF_DAY_HOURS) else 'PRESENT'
                note = '' if len(times) > 1 else 'No punch-out recorded'
                to_write.append((uid, day, status, first.time(),
                                 last.time() if len(times) > 1 else None, note))
                report['half' if status == 'HALF' else 'present'] += 1
            elif (uid, day) in on_leave:
                to_write.append((uid, day, 'LEAVE', None, None, 'Approved leave'))
                report['leave'] += 1
            elif mark_absent:
                to_write.append((uid, day, 'ABSENT', None, None, ''))
                report['absent'] += 1
            day += timedelta(days=1)

    with transaction.atomic():
        for uid, day, status, cin, cout, note in to_write:
            row = existing.get((uid, day)) or Attendance(
                user_id=uid, date=day, hospital=hospital)
            row.status = status
            row.check_in = cin
            row.check_out = cout
            row.notes = note
            row.source = Attendance.SOURCE_DEVICE
            row.save()

    logger.info('attendance rebuild %s..%s for hospital %s: %s',
                start, end, getattr(hospital, 'slug', None), report)
    return report


def preview_attendance(hospital, start, end):
    """The same computation without writing anything.

    The import screen shows this and asks. Attendance drives payroll; a bulk
    write into it that nobody was shown first is not something to offer.
    """
    from django.db import transaction

    class _Rollback(Exception):
        pass

    report = None
    try:
        with transaction.atomic():
            report = rebuild_attendance(hospital, start, end)
            raise _Rollback
    except _Rollback:
        pass
    return report
