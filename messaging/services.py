"""Sending an email or an SMS, and writing down what happened.

Two things shape this module:

* **Nothing may raise.** These are called from the middle of a booking, a
  discharge, a lab result. A gateway that is down, misconfigured or simply not
  bought must never lose the clinical work that triggered the message. Every
  send returns a `MessageLog` and swallows its exception into `status=FAILED`.
* **Not configured is not an error.** Most installs will have no SMS gateway,
  and the desktop/LAN build has no internet at all. Those cases record
  `SKIPPED`, so an outbox full of red does not train the admin to ignore it.

SMS has no single Pakistani standard, so rather than binding to one vendor the
generic backend is an **HTTP call described entirely by environment variables**
(`PHARMADOST_SMS_URL` and friends). Any gateway that accepts a GET/POST with a
number and a message — which is all of the local ones — can be plugged in
without touching code.
"""
import logging
from urllib import error as urlerror, parse, request as urlrequest

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from .models import MessageLog

log = logging.getLogger(__name__)

_TIMEOUT = 10


def email_configured():
    return bool(getattr(settings, 'EMAIL_HOST', '')
                and getattr(settings, 'DEFAULT_FROM_EMAIL', ''))


def sms_configured():
    return bool(getattr(settings, 'SMS_URL', ''))


def normalise_phone(number):
    """A Pakistani mobile in the form gateways expect: 923001234567.

    Reception types the number however it is written on the card — 0300…,
    +92 300…, 0092300…, with spaces or dashes. Reuses the same rules as the
    WhatsApp link filter so one number does not reach WhatsApp and fail SMS.
    """
    digits = ''.join(ch for ch in str(number or '') if ch.isdigit())
    if not digits:
        return ''
    # Strip an international dialling prefix FIRST, then decide. Testing for
    # '0092' and '0' in the same pass got 00923001234567 wrong: it removed all
    # four digits and left 3001234567 with no country code at all.
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('92'):
        return digits
    if digits.startswith('0'):
        return '92' + digits[1:]
    return '92' + digits


def _record(channel, to, subject, body, status, error='', kind='', dedupe_key=''):
    try:
        return MessageLog.objects.create(
            channel=channel, to=to or '', subject=subject or '', body=body or '',
            status=status, error=str(error)[:2000], kind=kind,
            dedupe_key=dedupe_key)
    except Exception:                      # pragma: no cover - logging must not break sending
        log.exception('could not write MessageLog')
        return None


def send_email(to, subject, body, kind='', dedupe_key='', html=False):
    """Send one email. Never raises."""
    if not to:
        return _record(MessageLog.EMAIL, to, subject, body, MessageLog.SKIPPED,
                       'no address', kind, dedupe_key)
    if not email_configured():
        return _record(MessageLog.EMAIL, to, subject, body, MessageLog.SKIPPED,
                       'email is not configured on this install', kind, dedupe_key)
    try:
        message = EmailMessage(subject=subject, body=body,
                               from_email=settings.DEFAULT_FROM_EMAIL,
                               to=[to], connection=get_connection())
        if html:
            message.content_subtype = 'html'
        message.send(fail_silently=False)
    except Exception as exc:
        log.warning('email to %s failed: %s', to, exc)
        return _record(MessageLog.EMAIL, to, subject, body, MessageLog.FAILED,
                       exc, kind, dedupe_key)
    return _record(MessageLog.EMAIL, to, subject, body, MessageLog.SENT,
                   '', kind, dedupe_key)


def _sms_request(number, text):
    """Build the gateway call from the env-described template.

    `SMS_PARAMS` is a query string with two placeholders the gateway's own
    parameter names are mapped onto, e.g.

        PHARMADOST_SMS_PARAMS=api_key=abc123&sender=Sehatyar&to={to}&text={text}

    so a different vendor is a change to `.env`, not to this file.
    """
    template = settings.SMS_PARAMS or 'to={to}&text={text}'
    payload = template.replace('{to}', parse.quote(number)) \
                      .replace('{text}', parse.quote(text))
    url = settings.SMS_URL
    if (settings.SMS_METHOD or 'GET').upper() == 'POST':
        return urlrequest.Request(
            url, data=payload.encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
    joiner = '&' if '?' in url else '?'
    return urlrequest.Request(f"{url}{joiner}{payload}")


def send_sms(to, text, kind='', dedupe_key=''):
    """Send one SMS through the configured HTTP gateway. Never raises."""
    number = normalise_phone(to)
    if not number:
        return _record(MessageLog.SMS, to, '', text, MessageLog.SKIPPED,
                       'no phone number', kind, dedupe_key)
    if not sms_configured():
        return _record(MessageLog.SMS, number, '', text, MessageLog.SKIPPED,
                       'no SMS gateway is configured on this install',
                       kind, dedupe_key)
    try:
        with urlrequest.urlopen(_sms_request(number, text), timeout=_TIMEOUT) as resp:
            reply = resp.read(500).decode('utf-8', 'replace')
            if resp.status >= 400:
                raise RuntimeError(f'HTTP {resp.status}: {reply}')
    except (urlerror.URLError, RuntimeError, OSError, ValueError) as exc:
        log.warning('sms to %s failed: %s', number, exc)
        return _record(MessageLog.SMS, number, '', text, MessageLog.FAILED,
                       exc, kind, dedupe_key)
    return _record(MessageLog.SMS, number, '', text, MessageLog.SENT,
                   '', kind, dedupe_key)


def already_sent(dedupe_key):
    """Has this exact message already gone out?

    Guards the reminder cron against messaging a patient twice when the task is
    re-run — the whole point of a shared host with one scheduled job is that you
    cannot be sure it ran exactly once.
    """
    if not dedupe_key:
        return False
    return MessageLog.objects.filter(dedupe_key=dedupe_key,
                                     status=MessageLog.SENT).exists()


def notify(*, email=None, phone=None, subject='', body='', sms_text=None,
           kind='', dedupe_key=''):
    """Send by whatever channels this recipient can be reached on.

    Returns the list of `MessageLog` rows written. `sms_text` defaults to the
    email body — an SMS is charged per 160 characters, so callers with anything
    long should pass a short form of their own.
    """
    results = []
    if email:
        results.append(send_email(email, subject, body, kind=kind,
                                  dedupe_key=f'{dedupe_key}:email' if dedupe_key else ''))
    if phone:
        results.append(send_sms(phone, sms_text if sms_text is not None else body,
                                kind=kind,
                                dedupe_key=f'{dedupe_key}:sms' if dedupe_key else ''))
    return [r for r in results if r is not None]
