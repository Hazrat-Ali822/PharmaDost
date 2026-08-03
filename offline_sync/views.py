"""The sync endpoint: a logged-in browser replays its offline queue here.

Each action is applied in its own transaction **together with** the ledger row
that records it, so a UUID can never be applied twice — not by a double-tap, not
by two tabs syncing at once, and not by a crash between the write and the
bookkeeping.
"""
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Notification
from accounts.replay import entry_made_at, replaying, summary_message

from .handlers import HANDLERS
from .models import ClientAction

# A single sync request carries at most this many actions; the client pages the
# rest into follow-up requests. Stops a device that was offline for a week from
# posting an unbounded batch in one shot.
MAX_BATCH = 200


class _AlreadyClaimed(Exception):
    """This UUID was applied (or is being applied) by another request."""


def _hospital_of(user):
    return None if user.is_superuser else getattr(user, "hospital", None)


def _error_text(exc):
    if isinstance(exc, ValidationError):
        try:
            return "; ".join(f"{k}: {', '.join(map(str, v))}"
                             for k, v in exc.message_dict.items())
        except Exception:
            return "; ".join(map(str, exc.messages))
    if isinstance(exc, Http404):
        return str(exc) or "The record this entry refers to no longer exists."
    return str(exc) or exc.__class__.__name__


def _dup_result(existing):
    if existing is None:      # lost a race and the winner rolled back — retry
        return {"uuid": "", "status": "error", "error": "in progress", "retry": True}
    return {"uuid": existing.client_uuid, "status": existing.status,
            "result": existing.result, "error": existing.error, "duplicate": True}


def _apply(request, uuid, kind, data):
    """Claim the UUID and apply the action inside one transaction.

    The claim insert comes first: the unique index on `client_uuid` is what
    serialises two concurrent replays of the same action — the loser blocks on
    the index, then fails, and is answered from the winner's stored result. If
    the handler then raises, the whole thing (claim included) rolls back, so the
    action is free to be retried or filed as a permanent failure.
    """
    with transaction.atomic():
        try:
            # Nested atomic: an IntegrityError must not poison the outer
            # transaction on PostgreSQL.
            with transaction.atomic():
                action = ClientAction.objects.create(
                    client_uuid=uuid, kind=kind, status=ClientAction.APPLIED,
                    hospital=_hospital_of(request.user), user=request.user)
        except IntegrityError:
            raise _AlreadyClaimed

        result = HANDLERS[kind](request, data)
        action.result = result or {}
        action.save(update_fields=["result"])
    return action.result


@require_POST
def sync(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=403)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"detail": "malformed JSON"}, status=400)

    actions = body.get("actions") or []
    results = []

    # Everything a replay raises is history, not new work — see accounts/replay.py.
    # Without this the desk's whole morning of offline entries rings the bell once
    # per entry the moment the internet returns, telling a doctor about patients
    # they saw hours ago and reception to allot a bed already occupied.
    with replaying() as session:
        _apply_batch(request, actions[:MAX_BATCH], results)
    _send_sync_summary(session)

    return JsonResponse({"results": results})


def _apply_batch(request, actions, results):
    for action in actions:
        uuid = (action.get("uuid") or "").strip()
        kind = action.get("kind")
        data = action.get("data") or {}
        # When the entry was actually typed on the device, so the notifications it
        # raises can say so instead of claiming to be about right now.
        entry_made_at(action.get("at"))

        if not uuid or kind not in HANDLERS:
            results.append({"uuid": uuid, "status": "failed",
                            "error": "unknown or malformed action", "permanent": True})
            continue

        # Already applied (or already permanently rejected)? Answer from the
        # ledger without touching the database again. The claim inside _apply is
        # the real guard; this is just the cheap common case.
        existing = ClientAction.objects.filter(client_uuid=uuid).first()
        if existing:
            results.append(_dup_result(existing))
            continue

        try:
            result = _apply(request, uuid, kind, data)
            results.append({"uuid": uuid, "status": "applied", "result": result})

        except _AlreadyClaimed:
            results.append(_dup_result(
                ClientAction.objects.filter(client_uuid=uuid).first()))

        except (ValidationError, PermissionDenied, Http404) as e:
            # Permanent: the data will not validate on a retry, or the record it
            # points at is gone. File it so the desk can see what bounced and
            # tell the client to stop retrying — a silent retry loop is how an
            # entry sits in the queue forever looking like it is about to sync.
            err = _error_text(e)
            try:
                ClientAction.objects.create(
                    client_uuid=uuid, kind=kind, status=ClientAction.FAILED,
                    hospital=_hospital_of(request.user), user=request.user, error=err)
            except IntegrityError:
                results.append(_dup_result(
                    ClientAction.objects.filter(client_uuid=uuid).first()))
                continue
            results.append({"uuid": uuid, "status": "failed",
                            "error": err, "permanent": True})

        except Exception as e:
            # Transient (deadlock, unexpected server error). Nothing was recorded
            # — the claim rolled back with the handler — so the client keeps the
            # action queued and retries when next online.
            results.append({"uuid": uuid, "status": "error",
                            "error": _error_text(e), "retry": True})


def _send_sync_summary(session):
    """One unread line per person, instead of one per queued entry.

    Sent after the replay window has closed, so the summary itself is delivered
    normally rather than being filed as history like the ones it stands for.
    """
    if not session or not session.user_counts:
        return
    span = session.span
    for user_pk, count in session.user_counts.items():
        user = session.users.get(user_pk)
        if user is None:
            continue
        Notification.objects.create(user=user, message=summary_message(count, span))


@require_GET
def ping(request):
    """Is the *server* actually reachable, not just the wifi?

    `navigator.onLine` is true whenever the device has a network link, which on a
    hospital router with a dead uplink is most of the time. The client hits this
    immediately before every offline-capable submit: 204 means submit normally,
    anything else (timeout, 5xx, a redirect to the login page) means queue it.

    Deliberately login-required and deliberately tiny — no template, no context
    processors, no queries.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=403)
    return JsonResponse({}, status=204)


def provisional_slip(request):
    """The slip reception hands a patient when the connection is down.

    This is the offline handoff, and it is the only one physics allows: two devices
    with no server between them cannot notify each other, so the doctor learns a
    patient has arrived because the patient walks in holding paper. The contents are
    read from the browser's own outbox — the entry has not reached the server yet —
    so this view only has to render the letterhead.
    """
    from django.shortcuts import render
    return render(request, "offline/provisional_slip.html")


def queue_page(request):
    """The outbox screen: what is still waiting, and what the server rejected.

    Everything on it is read from IndexedDB by the browser, so it opens with no
    connection — which is the only time it matters. Without this page a rejected
    entry is a six-second toast and then nothing.
    """
    from django.shortcuts import render
    return render(request, "offline/queue.html")
