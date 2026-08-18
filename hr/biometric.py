"""The endpoint a fingerprint terminal talks to, all by itself.

ZKTeco-style terminals (and the clones that copy them, which is most of what is
sold in Pakistan) speak a small HTTP protocol usually labelled **ADMS**, **Cloud
Server** or **Push** in the device menu. You type a server address and port into
the machine and it does the rest:

    GET  /iclock/cdata?SN=<serial>&options=all   -> configuration handshake
    POST /iclock/cdata?SN=<serial>&table=ATTLOG  -> tab-separated punch records
    GET  /iclock/getrequest?SN=<serial>          -> "any commands for me?"

Five things about this are load-bearing.

**The device dials out; nothing dials in.** A clinic's terminal sits behind a
home router with no public address, so polling it from the hosted site is
impossible without port forwarding — which is fragile and a hole. Dialling out
works from anywhere, and the *same* endpoint serves the desktop/LAN build, where
the address is `http://<lan-ip>:8000` and there is no internet at all.

**The paths are fixed by the firmware.** `/iclock/` is hard-coded in the device;
most firmwares only let you set an IP and a port. So these live at the site root
in `pharma_mgmt/urls.py`, not under `/hr/`.

**The serial number is the whole credential.** The protocol offers nothing else.
So `BiometricDevice.serial` is unique platform-wide and a device must be
registered before it is believed — and an unknown serial is *recorded*
(`UnknownDeviceContact`) rather than merely refused, because the commonest
setup mistake is one digit wrong and the alternative is a machine that shows a
tick while the server says nothing. Punches are attendance, not money or
credentials, and they cannot overwrite a hand-entered day; that is the
proportion this is judged at.

**These are exempt from the HTTPS redirect** (`SECURE_REDIRECT_EXEMPT` in
settings). Cheap terminals speak plain HTTP only, and `SECURE_SSL_REDIRECT`
answers them with a 301 they do not follow — the machine reports success, the
server logs a redirect, and nothing arrives. The exemption is scoped to
`/iclock/` alone.

**Never 500.** A terminal that gets an error retries the same buffer for ever,
and some firmwares wipe theirs on an unexpected reply. Anything unparseable is
skipped, logged and answered `OK`.
"""
import logging
from datetime import datetime

from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# What the handshake tells the device. Realtime=1 asks it to send each punch as
# it happens rather than in a nightly batch, which is the whole point.
_HANDSHAKE = (
    'GET OPTION FROM: {sn}\r\n'
    'Stamp=9999\r\n'
    'OpStamp=9999\r\n'
    'ErrorDelay=30\r\n'
    'Delay=10\r\n'
    'TransTimes=00:00;14:00\r\n'
    'TransInterval=1\r\n'
    'TransFlag=1111000000\r\n'
    'Realtime=1\r\n'
    'Encrypt=0\r\n'
    'TimeZone={tz}\r\n'
)


def _client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return (forwarded or request.META.get('REMOTE_ADDR') or '')[:45] or None


def _device(request):
    """The registered device this request is from, or None.

    `all_objects`: there is no session and no tenant bound — the device *is*
    how the tenant is resolved.
    """
    from .models import BiometricDevice
    serial = (request.GET.get('SN') or request.GET.get('sn') or '').strip()
    if not serial:
        return None
    return BiometricDevice.all_objects.filter(serial=serial, is_active=True).first()


def _record_stray(request):
    """Remember an unregistered serial so the admin can see what turned up."""
    from .models import UnknownDeviceContact
    serial = (request.GET.get('SN') or request.GET.get('sn') or '').strip()[:40]
    if not serial:
        return
    ip = _client_ip(request)
    try:
        with transaction.atomic():
            row, created = UnknownDeviceContact.objects.get_or_create(
                serial=serial, defaults={'ip': ip})
            if not created:
                UnknownDeviceContact.objects.filter(pk=row.pk).update(
                    hits=row.hits + 1, ip=ip, last_seen=timezone.now())
    except IntegrityError:                      # two pings racing; harmless
        pass
    logger.warning('biometric: unregistered device serial %r from %s', serial, ip)


def _seen(device, request, added=0):
    from .models import BiometricDevice
    BiometricDevice.all_objects.filter(pk=device.pk).update(
        last_seen=timezone.now(), last_ip=_client_ip(request),
        punches_received=device.punches_received + added)


def _parse_attlog(body):
    """Yield (device_user_id, naive datetime, raw line) from an ATTLOG body.

    Lines are tab-separated in the documented protocol and whitespace-separated
    in several firmwares that do not read it:

        1<TAB>2026-08-18 08:52:13<TAB>0<TAB>1<TAB>0<TAB>0

    A line that cannot be read is skipped rather than failing the batch — one
    malformed record must not cost the other two hundred in the same POST.
    """
    for line in (body or '').replace('\r', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t') if '\t' in line else line.split()
        if len(parts) < 3:
            logger.debug('biometric: unreadable ATTLOG line %r', line)
            continue
        uid = parts[0].strip()
        # Tab-form: parts[1] is "YYYY-MM-DD HH:MM:SS".
        # Space-form: it split into parts[1] date and parts[2] time.
        stamp = parts[1].strip()
        if ' ' not in stamp and len(parts) >= 3 and ':' in parts[2]:
            stamp = f'{stamp} {parts[2].strip()}'
        try:
            when = datetime.strptime(stamp[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                when = datetime.strptime(stamp[:16], '%Y-%m-%d %H:%M')
            except ValueError:
                logger.debug('biometric: unreadable timestamp %r', stamp)
                continue
        if not uid:
            continue
        yield uid, when, line[:200]


def _store(device, uid, naive_when, raw):
    """One punch. Returns True if it was new.

    The device sends wall-clock time in its own timezone; `USE_TZ` is on, so it
    has to be made aware before it is stored or every reading lands five hours
    out and a morning shift reads as the small hours.
    """
    from .attendance_build import resolve_user
    from .models import BiometricPunch

    tz = timezone.get_current_timezone()
    aware = timezone.make_aware(naive_when, tz, is_dst=None) \
        if timezone.is_naive(naive_when) else naive_when

    try:
        with transaction.atomic():
            BiometricPunch.all_objects.create(
                device=device, device_user_id=uid[:32], punched_at=aware,
                user=resolve_user(device.hospital, uid), raw=raw,
                hospital=device.hospital)
        return True
    except IntegrityError:
        # Already have it. Terminals resend their whole buffer after a network
        # drop, and several do it on a timer regardless.
        return False


@csrf_exempt
def cdata(request):
    """Handshake (GET) and punch delivery (POST). One path, as the firmware insists."""
    device = _device(request)
    if device is None:
        _record_stray(request)
        return HttpResponseForbidden('Unknown device')

    if request.method == 'GET':
        _seen(device, request)
        return HttpResponse(_HANDSHAKE.format(sn=device.serial,
                                              tz=device.timezone_offset),
                            content_type='text/plain')

    table = (request.GET.get('table') or '').upper()
    if table and table != 'ATTLOG':
        # OPERLOG (menu operations), USERINFO, FINGERTMP and friends. Accepted
        # and ignored: refusing them makes some firmwares retry for ever.
        _seen(device, request)
        return HttpResponse('OK', content_type='text/plain')

    try:
        body = request.body.decode('utf-8', errors='replace')
    except Exception:                          # noqa: BLE001 — never 500 at a device
        logger.exception('biometric: could not read body from %s', device.serial)
        return HttpResponse('OK', content_type='text/plain')

    added = 0
    for uid, when, raw in _parse_attlog(body):
        try:
            if _store(device, uid, when, raw):
                added += 1
        except Exception:                      # noqa: BLE001
            logger.exception('biometric: could not store punch %r', raw)

    _seen(device, request, added)
    logger.info('biometric: %s sent %s punch(es), %s new',
                device.serial, body.count('\n') + 1, added)
    return HttpResponse('OK', content_type='text/plain')


@csrf_exempt
def getrequest(request):
    """"Any commands for me?" — no, and saying so keeps the device polling."""
    device = _device(request)
    if device is None:
        _record_stray(request)
        return HttpResponseForbidden('Unknown device')
    _seen(device, request)
    return HttpResponse('OK', content_type='text/plain')


@csrf_exempt
def devicecmd(request):
    """The device reporting a command result. We issue none; acknowledge anyway."""
    device = _device(request)
    if device is None:
        _record_stray(request)
        return HttpResponseForbidden('Unknown device')
    _seen(device, request)
    return HttpResponse('OK', content_type='text/plain')
