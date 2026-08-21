"""Screens for the attendance machine: register it, map staff to it, build days.

The endpoint the terminal itself talks to is `hr/biometric.py`; nothing here is
reachable by a device. These are the three things a person has to do:

* **Register the machine** — its serial, and the address to type into it.
* **Map enrolment numbers to staff** — once, when people are enrolled.
* **Build the attendance days** — previewed first, because this writes the table
  payroll deducts from.

All of it is `feature_required('hr')` + ADMIN, the same gate as the rest of HR.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required, role_required

from .attendance_build import preview_attendance, rebuild_attendance, resolve_user
from .models import (BiometricDevice, BiometricPunch, StaffProfile,
                     UnknownDeviceContact)


def _hospital(request):
    return None if request.user.is_superuser else request.user.hospital


def _same_connection(a, b):
    """Is this stray device on the same network as the admin looking at the screen?

    The filter that makes showing unregistered serials safe. The serial is the
    only credential the protocol has, so a list of everyone's strays would let
    one hospital claim another's machine and start collecting its attendance.
    A clinic's terminal and a clinic's admin come from the same connection: the
    same public address on the hosted site, the same /24 on a LAN.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    pa, pb = a.split('.'), b.split('.')
    return len(pa) == len(pb) == 4 and pa[:3] == pb[:3]


def strays_for(request):
    """Unregistered serials seen from the admin's own connection, recently."""
    from audit.middleware import get_current_ip
    mine = get_current_ip()
    cutoff = timezone.now() - timedelta(days=7)
    return [s for s in UnknownDeviceContact.objects.filter(last_seen__gte=cutoff)
            if _same_connection(mine, s.ip)]


def _server_address(request):
    """What to type into the machine. Shown, never guessed at by the user.

    The device wants a bare host and a port, not a URL — and it is the single
    thing people get wrong, because the obvious answer (the address in the
    browser bar) is right on a LAN and right on the hosted site but written
    differently in each.
    """
    host = request.get_host()
    if ':' in host:
        name, port = host.rsplit(':', 1)
    else:
        name, port = host, '443' if request.is_secure() else '80'
    return {'host': name, 'port': port, 'secure': request.is_secure()}


@feature_required('biometric')
@role_required(['ADMIN'])
def device_list(request):
    hospital = _hospital(request)
    if request.method == 'POST':
        return _device_save(request, hospital)

    devices = list(BiometricDevice.all_objects.filter(hospital=hospital))
    unmapped = (BiometricPunch.all_objects
                .filter(hospital=hospital, user__isnull=True)
                .values_list('device_user_id', flat=True).distinct())
    return render(request, 'hr/biometric_devices.html', {
        'devices': devices,
        # Ticks step 2. "Registered" and "actually reachable" are different
        # things, and the gap between them is where every setup stalls.
        'any_contact': any(d.has_ever_contacted for d in devices),
        'strays': strays_for(request),
        'server': _server_address(request),
        'unmapped': sorted(set(unmapped)),
        'unmapped_count': BiometricPunch.all_objects.filter(
            hospital=hospital, user__isnull=True).count(),
    })


def _device_save(request, hospital):
    action = request.POST.get('action')
    if action == 'delete':
        device = get_object_or_404(BiometricDevice.all_objects,
                                   pk=request.POST.get('pk'), hospital=hospital)
        name = device.name
        device.delete()
        messages.success(request, f'{name} removed. Its scans went with it.')
        return redirect('hr_biometric_devices')

    serial = (request.POST.get('serial') or '').strip()
    name = (request.POST.get('name') or '').strip() or 'Attendance machine'
    if not serial:
        messages.error(request, 'The serial number is what identifies the machine — it cannot be blank.')
        return redirect('hr_biometric_devices')

    # Unique platform-wide, because it is the credential. Say so plainly rather
    # than letting the database raise: the likeliest cause by far is a typo.
    clash = BiometricDevice.all_objects.filter(serial=serial).first()
    if clash is not None:
        if clash.hospital_id == getattr(hospital, 'id', None):
            messages.info(request, f'{serial} is already registered here as "{clash.name}".')
        else:
            messages.error(request, f'Serial {serial} is already registered. '
                                    'Check the number on the machine.')
        return redirect('hr_biometric_devices')

    BiometricDevice.all_objects.create(
        hospital=hospital, serial=serial, name=name,
        location=(request.POST.get('location') or '').strip())
    UnknownDeviceContact.objects.filter(serial=serial).delete()
    messages.success(request, f'"{name}" registered. Type this server\'s address '
                              'into the machine and it will start sending on its own.')
    return redirect('hr_biometric_devices')


@feature_required('biometric')
@role_required(['ADMIN'])
def enrolment_map(request):
    """Which enrolment number on the machine is which member of staff.

    The one manual step, done once. Numbers seen in punches but not mapped are
    listed at the top — that is the whole reason unmapped punches are kept
    rather than dropped.
    """
    hospital = _hospital(request)
    profiles = list(StaffProfile.all_objects.filter(hospital=hospital)
                    .select_related('user').order_by('user__email'))

    if request.method == 'POST':
        taken = {}
        for p in profiles:
            value = (request.POST.get(f'bio_{p.pk}') or '').strip()
            if value and value in taken:
                messages.error(request, f'Enrolment number {value} is on two people — '
                                        'the machine only has one of each.')
                return redirect('hr_biometric_enrolment')
            if value:
                taken[value] = p.pk
            if value != (p.biometric_id or ''):
                p.biometric_id = value
                p.save(update_fields=['biometric_id'])
        linked = _relink(hospital)
        messages.success(request, f'Saved. {linked} scan(s) matched to a person.'
                         if linked else 'Saved.')
        return redirect('hr_biometric_enrolment')

    counts = {}
    for uid in BiometricPunch.all_objects.filter(
            hospital=hospital, user__isnull=True).values_list('device_user_id', flat=True):
        counts[uid] = counts.get(uid, 0) + 1
    return render(request, 'hr/biometric_enrolment.html', {
        'profiles': profiles,
        'unmapped': sorted(counts.items(), key=lambda kv: -kv[1]),
    })


def _relink(hospital):
    """Attach punches that were waiting for a mapping. Returns how many."""
    pending = BiometricPunch.all_objects.filter(hospital=hospital, user__isnull=True)
    linked = 0
    cache = {}
    for punch in pending:
        uid = punch.device_user_id
        if uid not in cache:
            cache[uid] = resolve_user(hospital, uid)
        user = cache[uid]
        if user is not None:
            BiometricPunch.all_objects.filter(pk=punch.pk).update(user=user)
            linked += 1
    return linked


@feature_required('biometric')
@role_required(['ADMIN'])
def build_attendance(request):
    """Preview, then write. Never the other way round.

    Attendance is what payroll deducts from, so this shows what it is about to
    do — including how many days the machine appears to have been switched off,
    which are the days a careless import would mark the whole staff absent.
    """
    hospital = _hospital(request)
    today = timezone.localdate()
    first = today.replace(day=1)

    start = _date(request.POST.get('start') or request.GET.get('start'), first)
    end = _date(request.POST.get('end') or request.GET.get('end'), today)

    report = None
    if request.method == 'POST' and request.POST.get('action') == 'apply':
        report = rebuild_attendance(hospital, start, end)
        messages.success(
            request,
            f"Attendance built for {start:%d/%m/%Y}–{end:%d/%m/%Y}: "
            f"{report['present']} present, {report['half']} half, "
            f"{report['leave']} leave, {report['absent']} absent. "
            f"{report['skipped_manual']} hand-entered day(s) left alone.")
        return redirect(f"{request.path}?start={start}&end={end}&done=1")
    if request.method == 'POST':
        report = preview_attendance(hospital, start, end)

    return render(request, 'hr/biometric_build.html', {
        'start': start, 'end': end, 'report': report,
        'done': request.GET.get('done'),
        'scan_count': BiometricPunch.all_objects.filter(
            hospital=hospital, punched_at__date__gte=start,
            punched_at__date__lte=end).count(),
    })


@feature_required('biometric')
@role_required(['ADMIN'])
def scan_list(request):
    """The raw events, newest first — for the day somebody disputes a deduction."""
    hospital = _hospital(request)
    qs = (BiometricPunch.all_objects.filter(hospital=hospital)
          .select_related('user', 'device')[:300])
    return render(request, 'hr/biometric_scans.html', {'scans': qs})


def _date(raw, default):
    from datetime import datetime
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date() if raw else default
    except (TypeError, ValueError):
        return default
