"""Slow down automated walking of the public patient portal lookup.

`/portal/` is anonymous and, given the right number, hands back a medical
record. It is scoped to one hospital and refuses a bare name, but **MRNs are
issued in sequence**, so inside a single hospital they can still be counted
through. A rate limit is what makes that impractical.

**Counted in the database, not the cache** — the same decision, for the same
reason, as `accounts/lockout.py`:

    the host runs Passenger with more than one process, and Django's default
    cache is per-process local memory

and no `CACHES` setting is configured, so `cache.get`/`cache.set` land in
`LocMemCache`. A cache counter therefore gives an attacker *N attempts per
worker*, resetting whenever a worker recycles — which reads as a working
throttle in a single-process dev server and is a fraction of one in production.
`accounts/lockout.py` reaches for `AuditLog` rows instead; this cannot, because
`AuditLog` is the admin's security feed and filling it with routine patient
lookups is precisely what CLAUDE.md says not to do. Hence one small table of
its own.

**It fails open.** If the throttle itself errors, the patient is let through.
A broken security counter must not become an outage on the one page a patient
uses when they have lost their slip.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def _max_attempts():
    return int(getattr(settings, 'PORTAL_LOOKUP_MAX', 20))


def _window_seconds():
    return int(getattr(settings, 'PORTAL_LOOKUP_WINDOW_SECONDS', 60))


def client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return (forwarded or request.META.get('REMOTE_ADDR') or '')[:45]


def too_many(ip):
    """Has this address already used up its searches for the window?"""
    if not ip:
        return False
    from .models import PortalLookupAttempt

    try:
        since = timezone.now() - timedelta(seconds=_window_seconds())
        return (PortalLookupAttempt.objects
                .filter(ip=ip, at__gte=since)
                .count()) >= _max_attempts()
    except Exception:
        return False           # fail open — never lock a patient out on our bug


def record(ip):
    """Note one search, and clear out rows nobody will count again.

    The prune is opportunistic rather than a cron job: this table exists only
    to answer "how many in the last minute", so anything older is dead weight
    and there is no reason to keep a scheduled task alive for it.
    """
    if not ip:
        return
    from .models import PortalLookupAttempt

    try:
        PortalLookupAttempt.objects.create(ip=ip)
        cutoff = timezone.now() - timedelta(seconds=_window_seconds() * 10)
        PortalLookupAttempt.objects.filter(at__lt=cutoff).delete()
    except Exception:
        pass
