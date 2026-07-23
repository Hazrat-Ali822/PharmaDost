from django.conf import settings
from django.db import models
from django.utils import timezone

from saas.utils import TenantManager


class AuditLog(models.Model):
    """Immutable record of who did what, when — for accountability & security.

    Business-model create/update/delete are captured automatically via signals
    (audit/signals.py) using the request user (audit/middleware.py). Auth events
    (login / logout / failed login) are captured too.

    Tenant-scoped. Without the `hospital` column and `TenantManager` below, one
    hospital's admin could read every other tenant's trail — patient names, sales,
    staff logins — from `/audit/`, which is the single most sensitive page in the
    product.
    """

    ACTIONS = (
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('LOGIN_FAILED', 'Login failed'),
    )

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='audit_logs')
    action = models.CharField(max_length=20, choices=ACTIONS)
    model_name = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='audit_logs')

    objects = TenantManager()
    # Unscoped, for the superuser SaaS portal and management commands.
    all_objects = models.Manager()

    class Meta:
        ordering = ('-timestamp',)

    def __str__(self):
        who = self.user or 'system'
        return f'{self.timestamp:%Y-%m-%d %H:%M} · {who} · {self.action} {self.model_name}'
