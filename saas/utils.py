import threading
from django.db import models

_thread_locals = threading.local()


def get_current_hospital():
    return getattr(_thread_locals, 'hospital', None)


def set_current_hospital(hospital):
    _thread_locals.hospital = hospital


def is_tenant_strict():
    """True while serving a request on behalf of a non-superuser.

    See `TenantManager` — this is what makes tenant filtering fail CLOSED for a
    logged-in user who has no hospital, without breaking management commands.
    """
    return getattr(_thread_locals, 'tenant_strict', False)


def set_tenant_strict(strict):
    _thread_locals.tenant_strict = bool(strict)


def clear_current_hospital():
    for attr in ('hospital', 'tenant_strict'):
        if hasattr(_thread_locals, attr):
            delattr(_thread_locals, attr)


class TenantManager(models.Manager):
    """Scopes a model's default queryset to the hospital bound to this thread.

    Three cases, in order:

    1. A hospital is bound -> filter to it. The normal request path.
    2. No hospital, but we are inside a request for a non-superuser ("strict")
       -> filter to `hospital IS NULL`. A logged-in user whose `hospital` is None
       must see only hospital-less rows, never every tenant's data. Without this
       the manager is fail-OPEN and such a user reads across all hospitals — a
       real cross-tenant leak of patient records, which this guards against.
    3. No hospital and not strict -> unfiltered. Management commands, cron jobs
       and the superuser SaaS portal legitimately operate across all tenants.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        hospital = get_current_hospital()
        if hospital:
            return qs.filter(hospital=hospital)
        if is_tenant_strict():
            return qs.filter(hospital__isnull=True)
        return qs
