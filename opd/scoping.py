"""Tenant scoping for `Doctor`, which carries a `hospital` column but no
`TenantManager` — every view that lists or fetches a doctor narrows the queryset
itself, and a view that forgets is a cross-tenant leak with nothing to catch it.
That is not hypothetical: the payout screens (`/opd/payouts/`) queried
`Doctor.objects.all()`, so one hospital's admin could read another hospital's
doctors' earnings *and* record a payout against them.

Giving that filter a name is the fix. Import it rather than re-rolling the `Q`.

**This used to scope through `user__hospital`, and that leaked.** A `Doctor` is a
roster entry, and most of them have no login at all — the user field on
`/opd/doctors/add/` is optional and normally left blank. So the filter had to
carry `| Q(user__isnull=True)` to keep those rows visible to their own hospital,
which made them visible to *every* hospital: a customer's OPD board listed the
demo tenant's doctors, and its payout CSV exported them with their balances.
`Doctor.hospital` exists so those rows can be told apart; do not go back.
"""
from .models import Doctor


def scoped_doctors(user, qs=None):
    """The doctors `user` may see — fail-closed.

    Keyed on `is_superuser`, never on "does this user have a hospital": a
    hospital-less non-superuser must match only hospital-less rows, not every
    tenant's (CLAUDE.md, "Multi-tenancy"). A hospital-less install — the
    desktop/LAN build — therefore keeps working on its own `hospital IS NULL`
    roster.
    """
    qs = Doctor.objects.all() if qs is None else qs
    if user is not None and user.is_superuser:
        return qs
    return qs.filter(hospital=getattr(user, 'hospital', None))
