"""The sync endpoint: a logged-in browser replays its offline queue here.

Each action is deduplicated by its client UUID and applied in its own
transaction, so one bad action cannot sink the rest of the batch, and a replayed
UUID never produces a second record.
"""
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .handlers import HANDLERS
from .models import ClientAction

# A single sync request carries at most this many actions; the client pages the
# rest into follow-up requests. Stops a device that was offline for a week from
# posting an unbounded batch in one shot.
MAX_BATCH = 200


def _hospital_of(user):
    return None if user.is_superuser else getattr(user, "hospital", None)


def _error_text(exc):
    if isinstance(exc, ValidationError):
        try:
            return "; ".join(f"{k}: {', '.join(map(str, v))}"
                             for k, v in exc.message_dict.items())
        except Exception:
            return "; ".join(map(str, exc.messages))
    return str(exc) or exc.__class__.__name__


def _dup_result(existing):
    return {"uuid": existing.client_uuid, "status": existing.status,
            "result": existing.result, "error": existing.error, "duplicate": True}


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

    for action in actions[:MAX_BATCH]:
        uuid = (action.get("uuid") or "").strip()
        kind = action.get("kind")
        data = action.get("data") or {}

        if not uuid or kind not in HANDLERS:
            results.append({"uuid": uuid, "status": "failed",
                            "error": "unknown or malformed action", "permanent": True})
            continue

        # Already applied (or already permanently rejected)? Return the stored
        # outcome without touching the database again.
        existing = ClientAction.objects.filter(client_uuid=uuid).first()
        if existing:
            results.append(_dup_result(existing))
            continue

        try:
            with transaction.atomic():
                result = HANDLERS[kind](request, data)
            try:
                ClientAction.objects.create(
                    client_uuid=uuid, kind=kind, status=ClientAction.APPLIED,
                    hospital=_hospital_of(request.user), user=request.user,
                    result=result)
            except IntegrityError:
                pass  # a racing tab logged it first — the record itself is committed
            results.append({"uuid": uuid, "status": "applied", "result": result})

        except (ValidationError, PermissionDenied) as e:
            # Permanent: the data will not validate on a retry. File it so the
            # desk can see what bounced, and tell the client to stop retrying.
            err = _error_text(e)
            try:
                ClientAction.objects.create(
                    client_uuid=uuid, kind=kind, status=ClientAction.FAILED,
                    hospital=_hospital_of(request.user), user=request.user, error=err)
            except IntegrityError:
                existing = ClientAction.objects.filter(client_uuid=uuid).first()
                if existing:
                    results.append(_dup_result(existing))
                    continue
            results.append({"uuid": uuid, "status": "failed",
                            "error": err, "permanent": True})

        except Exception as e:
            # Transient (deadlock, unexpected server error). Do NOT record it —
            # the client keeps the action queued and retries when next online.
            results.append({"uuid": uuid, "status": "error",
                            "error": _error_text(e), "retry": True})

    return JsonResponse({"results": results})
