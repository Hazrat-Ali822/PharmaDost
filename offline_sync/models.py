from django.conf import settings
from django.db import models


class ClientAction(models.Model):
    """Idempotency ledger for actions a browser queued while offline and replayed
    on reconnect.

    The client stamps every queued action with a UUID before it is saved to the
    device. When the browser comes back online it POSTs the queue to the sync
    endpoint; this row records that a given UUID has already been applied, so a
    retry — a double-tap, a flaky sync, two open tabs — is deduplicated to the
    first result instead of creating a second patient, token or sale.

    The sync request is made by the logged-in browser (same-origin, with the
    session cookie), so `hospital`/`user` come from the live session and normal
    tenant scoping applies. This is deliberately NOT a headless background job:
    there is always an authenticated request behind an apply.

    A row that is `FAILED` is a *permanent* rejection (the data did not validate)
    kept so the desk can see what bounced and re-enter it. A transient server
    error is not recorded at all — the client keeps the action queued and retries.
    """

    APPLIED = "applied"
    FAILED = "failed"
    STATUS_CHOICES = [(APPLIED, "Applied"), (FAILED, "Failed")]

    client_uuid = models.CharField(max_length=64, unique=True, db_index=True)
    kind = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    hospital = models.ForeignKey(
        "saas.Hospital", on_delete=models.CASCADE,
        null=True, blank=True, related_name="+")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+")
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind}:{self.client_uuid[:8]} ({self.status})"
