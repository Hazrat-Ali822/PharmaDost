"""Stop a password from being guessed indefinitely.

`audit/signals.py` already *notices* a burst of failed sign-ins and tells the
admin about it — but noticing is not stopping. Nothing in the app refused a
further attempt, so an internet-facing hospital login could be tried at machine
speed for as long as anyone liked, and the only consequence was a notification
the admin might read tomorrow.

Counting is done off `AuditLog`'s existing failed-login rows rather than a cache.
That is deliberate: the host runs Passenger with **more than one process**, and
Django's default cache is per-process local memory, so a cache-based counter
would let an attacker get N attempts per worker and reset whenever a worker
recycled. The audit rows are shared, already written, already indexed by time,
and already the thing the admin is shown.

Locking is by **(email, IP) pair**, not by account alone. Locking the account
alone hands anyone a way to lock a hospital's staff out of their own system by
typing a wrong password a few times — a denial of service dressed as security.
Someone on the attacker's IP is stopped; the real nurse on the ward wifi is not.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def _threshold():
    return int(getattr(settings, 'LOCKOUT_THRESHOLD', 8))


def _window():
    return int(getattr(settings, 'LOCKOUT_WINDOW_MINUTES', 15))


def _lock_minutes():
    return int(getattr(settings, 'LOCKOUT_MINUTES', 15))


def client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return forwarded or request.META.get('REMOTE_ADDR') or ''


def _recent_failures(email, ip):
    """Failed sign-ins for this (email, IP) inside the window."""
    from audit.models import AuditLog

    since = timezone.now() - timedelta(minutes=_window())
    qs = AuditLog.all_objects.filter(action='LOGIN_FAILED', timestamp__gte=since)
    if email:
        qs = qs.filter(object_repr__icontains=email)
    if ip:
        qs = qs.filter(ip_address=ip)
    return qs


def failure_count(email, ip):
    return _recent_failures(email, ip).count()


def is_locked(email, ip):
    """Is this (email, IP) currently shut out, and for how much longer?

    Returns `(locked, minutes_left)`. The lock lifts on its own — there is no
    unlock button and no stored flag to clear, because an admin who is himself
    locked out cannot press one.
    """
    if not _threshold():
        return False, 0
    failures = _recent_failures(email, ip).order_by('-timestamp')[:_threshold()]
    failures = list(failures)
    if len(failures) < _threshold():
        return False, 0
    # The lock runs from the most recent failure, so continuing to hammer it
    # keeps it shut rather than waiting the original window out.
    until = failures[0].timestamp + timedelta(minutes=_lock_minutes())
    remaining = (until - timezone.now()).total_seconds()
    if remaining <= 0:
        return False, 0
    return True, max(1, int(remaining // 60) + 1)


def lockout_message(minutes_left):
    return (f"Too many failed sign-in attempts. Please try again in "
            f"{minutes_left} minute{'s' if minutes_left != 1 else ''}, or ask "
            f"your hospital administrator to reset your password.")


def guard(request):
    """Call at the top of every sign-in entry point.

    Returns a rendered "locked out" response when this (email, IP) has run out
    of attempts, or None to let the sign-in proceed. Only POSTs are counted and
    only POSTs are blocked — someone who is locked out can still *see* the page,
    which is where the explanation is.

    A dedicated template rather than an error on the form: there are two login
    templates (the platform one and each tenant's branded one) and a third plain
    one on the desktop build, and a guard that has to be wired into each of them
    is a guard that will be missing from one of them.
    """
    from django.shortcuts import render

    if request.method != 'POST':
        return None
    email = (request.POST.get('username') or request.POST.get('email') or '').strip()
    if not email:
        return None
    locked, minutes = is_locked(email, client_ip(request))
    if not locked:
        return None
    return render(request, 'registration/locked_out.html',
                  {'minutes': minutes, 'message': lockout_message(minutes)},
                  status=429)
