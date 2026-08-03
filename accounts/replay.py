"""Keeping a synced offline queue from setting off a notification storm.

The problem this solves is real and easy to miss. A desk works through an outage:
ten patients registered and booked, an admission advised, a surgery advised. Every
one of those raises a notification — "New Patient assigned, Token 5",
"🛏️ Admission advised: please allot a bed" — because the live path does. Those
notifications are created at the moment the queue is **replayed**, not at the moment
the work happened, so the instant the internet returns the doctor gets ten pings
about patients they saw hours ago and reception is told to allot a bed that is
already occupied. Work that is finished reads as work that is waiting.

So during a replay, notifications are:

  * **stamped with the time the entry was actually made** ("⏱ 10:32 offline —"), so
    the message stops claiming to be about now; and
  * **filed as already read**, so the bell does not ring once per queued entry —
    nothing is deleted, the detail is all still there to scroll through.

Then `offline_sync.views.sync` sends **one** unread summary per affected person:
"14 entries made offline (10:05–13:40) have synced." That is the only thing anyone
actually needs to look at, and it is honest about being history.

The exception is a notification about work that is **still outstanding** — a sale
that dispensed short stock still needs the pharmacist to count the shelf, whenever it
was made. Those pass `force=True` and stay unread.
"""
import threading
from contextlib import contextmanager

from django.utils import timezone
from django.utils.dateparse import parse_datetime

_state = threading.local()


class ReplaySession:
    """What one sync batch did, gathered for the summary at the end."""

    def __init__(self):
        self.user_counts = {}     # user pk -> how many notifications were held back
        self.users = {}           # user pk -> User (kept so we don't re-query)
        self.times = []           # when the entries were actually made, on the device
        self.entered_at = None    # the entry currently being replayed

    def held(self, notification):
        pk = notification.user_id
        self.user_counts[pk] = self.user_counts.get(pk, 0) + 1
        self.users.setdefault(pk, notification.user)

    def note_time(self, when):
        if when:
            self.times.append(when)

    @property
    def span(self):
        """'10:05–13:40', or a single time, or '' when the device sent none."""
        if not self.times:
            return ""
        first, last = min(self.times), max(self.times)
        fmt = "%H:%M"
        a = timezone.localtime(first).strftime(fmt)
        b = timezone.localtime(last).strftime(fmt)
        return a if a == b else f"{a}–{b}"


def current():
    return getattr(_state, "session", None)


@contextmanager
def replaying():
    """Mark this thread as replaying a queue. Nested use keeps the outer session."""
    existing = current()
    if existing is not None:
        yield existing
        return
    session = ReplaySession()
    _state.session = session
    try:
        yield session
    finally:
        _state.session = None


def entry_made_at(iso_string):
    """Tell the session when the entry being replayed was typed on the device.

    The client stamps every queued action; without it the summary can only say
    "some entries synced", which is exactly the vagueness that makes people ignore
    it. A malformed or missing value is not worth failing a sync over.
    """
    session = current()
    if session is None:
        return
    when = parse_datetime(iso_string) if iso_string else None
    if when and timezone.is_naive(when):
        when = timezone.make_aware(when, timezone.get_default_timezone())
    session.entered_at = when
    session.note_time(when)


def stamp(notification):
    """Called from `Notification.save()`. Returns True if this is a replay.

    Rewrites the message to say when it happened and marks it read, unless the
    caller flagged it as still needing action.
    """
    session = current()
    if session is None:
        return False

    when = session.entered_at
    if when is not None:
        prefix = f"⏱ {timezone.localtime(when).strftime('%H:%M')} offline — "
    else:
        prefix = "⏱ offline — "
    if not notification.message.startswith("⏱"):
        notification.message = (prefix + notification.message)[:255]

    if not getattr(notification, "needs_action", False):
        notification.is_read = True
        session.held(notification)
    return True


def summary_message(count, span):
    """One line that says what happened without pretending it is new work."""
    when = f" ({span})" if span else ""
    entries = "entry" if count == 1 else "entries"
    return (
        f"📥 {count} {entries} made offline{when} synced just now. They were handled "
        f"at the desk while the connection was down — nothing to do unless something "
        f"looks missing."
    )[:255]
