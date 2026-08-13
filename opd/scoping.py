"""Tenant scoping for `Doctor`, which has no `hospital` column of its own.

`Doctor` is reached through its linked user account, so `TenantManager` cannot
help it — every view that lists or fetches a doctor has to narrow the queryset
itself, and a view that forgets is a cross-tenant leak with nothing to catch it.
That is not hypothetical: the payout screens (`/opd/payouts/`) queried
`Doctor.objects.all()`, so one hospital's admin could read another hospital's
doctors' earnings *and* record a payout against them.

Giving that filter a name is the fix. Import it rather than re-rolling the `Q`.
"""
from django.db.models import Q

from .models import Doctor


def scoped_doctors(user, qs=None):
    """The doctors `user` may see — fail-closed.

    Keyed on `is_superuser`, never on "does this user have a hospital": a
    hospital-less non-superuser must match only hospital-less rows, not every
    tenant's (CLAUDE.md, "Multi-tenancy").

    Roster rows with no linked user account belong to nobody in particular and
    stay visible to everyone — the same rule `opd.availability` uses.
    """
    qs = Doctor.objects.all() if qs is None else qs
    if user is not None and user.is_superuser:
        return qs
    hospital = getattr(user, 'hospital', None)
    return qs.filter(Q(user__hospital=hospital) | Q(user__isnull=True))
