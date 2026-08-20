"""One base class so a dropdown cannot quietly offer another hospital's rows.

**The bug this exists for.** A plain `forms.ModelForm` with a foreign key builds
that field's queryset from the model's default manager **when the class is
defined** — i.e. at import, on the first request the worker ever serves, with no
tenant bound. `TenantManager` is not strict then (nothing is, outside a request),
so it hands back every hospital's rows, and that one queryset object is reused
for the life of the process. Nothing in the form's own code looks wrong; the
leak is in *when* Django evaluated it.

`inventory.MedicineForm` was the one that showed: the Supplier dropdown on
`/medicines/add/` listed the demo tenant's distributors to a real customer, and
`Supplier` had a `hospital` column and a `TenantManager` all along.

CLAUDE.md already warned about the hand-written form of this ("a form field's
queryset belongs in `__init__`, never at class level"). What it missed is that
Django writes the class-level version for you, for **every** FK, in every plain
ModelForm — so remembering the rule was never going to be enough.

**The fix.** `TenantModelForm.__init__` re-applies the tenant filter to every
choice field pointing at a model that has a `hospital` column. It *narrows* the
existing queryset rather than replacing it, so a form that deliberately filters
(`is_active=True`, a role, a date) keeps its filter and gains the tenant one.

A subclass that sets a queryset in its own `__init__` runs after this and is
unaffected — correctly, because a manager called during a request is already
scoped. The one model where that is not true is `Doctor`, which has the column
but no `TenantManager`; those callers go through `opd.scoping.scoped_doctors`,
and this is a second net under them.

Guarded by `tests/test_tenant_forms.py`, which builds every ModelForm in the
project with one tenant bound and fails if any dropdown offers another
hospital's row. That test is the rule; this class is only how it is kept.
"""
from django import forms

from .utils import get_current_hospital, is_tenant_strict


def scope_queryset(qs):
    """Narrow `qs` to the current tenant — the three cases `TenantManager` uses.

    Kept in step with `saas.utils.TenantManager.get_queryset` and
    `inventory.models._scope_to_tenant`. Never reduce it to `if hospital:`: a
    signed-in non-superuser with no hospital must match `hospital IS NULL`, not
    everything.
    """
    hospital = get_current_hospital()
    if hospital:
        return qs.filter(hospital=hospital)
    if is_tenant_strict():
        return qs.filter(hospital__isnull=True)
    return qs


def _is_tenant_model(model):
    return any(f.name == 'hospital' for f in model._meta.fields)


def scope_choice_fields(form):
    """Apply `scope_queryset` to every tenant-scoped dropdown on `form`.

    Covers `ModelChoiceField` and `ModelMultipleChoiceField` alike — a
    multi-select validates POSTed ids against its own queryset just as a single
    one does, so an unscoped multi-select is a cross-tenant *write* path.
    """
    for field in form.fields.values():
        qs = getattr(field, 'queryset', None)
        if qs is None or not _is_tenant_model(qs.model):
            continue
        field.queryset = scope_queryset(qs)


class TenantModelForm(forms.ModelForm):
    """Use this instead of `forms.ModelForm` anywhere in this project."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_choice_fields(self)
