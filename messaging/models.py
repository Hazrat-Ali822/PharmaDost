"""A record of every message the system tried to send.

There is **no job queue** on this deployment — a shared cPanel host with one
scheduled task — so messages are sent inline, and an inline send that fails has
nowhere to retry from and nobody watching. Without a log, "the patient never got
their reminder" is unanswerable: you cannot tell whether the message was never
composed, was composed and rejected by the gateway, or was delivered and
ignored.

So every attempt is written here, success or failure, with the gateway's own
error text. `send_reminders` also reads it back to avoid sending the same
reminder twice — that de-duplication is the log's second job and the reason
`dedupe_key` is indexed.
"""
from django.db import models
from django.utils import timezone

from saas.utils import TenantManager


class MessageLog(models.Model):
    EMAIL = 'EMAIL'
    SMS = 'SMS'
    CHANNEL_CHOICES = ((EMAIL, 'Email'), (SMS, 'SMS'))

    SENT = 'SENT'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'
    STATUS_CHOICES = (
        (SENT, 'Sent'),
        (FAILED, 'Failed'),
        # Nothing was wrong — there was simply no address/number to send to, or
        # the channel is not configured on this install. Distinguished from
        # FAILED so a hospital with no SMS gateway does not read as broken.
        (SKIPPED, 'Skipped'),
    )

    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    to = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    error = models.TextField(blank=True)
    # What this message was for ('appointment_reminder', 'lab_ready', ...) —
    # lets the outbox screen and the reminder command filter by purpose.
    kind = models.CharField(max_length=50, blank=True, db_index=True)
    # Stable identity of "this message, for this thing, on this day". The
    # reminder command refuses to send when a SENT row already carries the same
    # key, so re-running the cron (or running it twice by accident) does not
    # message a patient again.
    dedupe_key = models.CharField(max_length=200, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True)

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"{self.channel} to {self.to} ({self.status})"
