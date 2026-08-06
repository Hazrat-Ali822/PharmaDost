"""Tenant teardown.

Deleting a whole hospital is not a single `hospital.delete()` cascade: several
child rows hold **PROTECT** foreign keys to their parents (SaleItem→Medicine and
→StockBatch, Invoice→Patient, Sale→Customer/Panel, EmergencyCase→Patient,
Pregnancy/Delivery→Patient, VaccinationRecord→Vaccine, StockAdjustment→StockBatch,
…). A plain cascade from Hospital hits those and raises `ProtectedError`.

`purge_tenant` wipes every hospital-scoped row in **repeated passes**: each pass
deletes the rows that are no longer protected (leaf rows first — a Sale carries
its SaleItems away by cascade, which unprotects the Medicine), which unblocks
their parents for the next pass. It converges without hard-coding the dependency
order, so new models with a `hospital` FK are handled automatically.

`User` and `Hospital` are deliberately left for the caller: the SaaS delete view
captures staff ids to remove after the cascade (User.hospital is SET_NULL), and
the demo reset deletes only its own `DEMO_*` accounts.
"""
from django.apps import apps
from django.db import transaction
from django.db.models import ProtectedError

_MAX_PASSES = 15


def _hospital_scoped_models():
    """Every concrete model with a real FK named `hospital` to saas.Hospital,
    excluding User and Hospital themselves."""
    out = []
    for model in apps.get_models():
        if model._meta.label in ('saas.Hospital',) or model._meta.label_lower == 'accounts.user':
            continue
        try:
            field = model._meta.get_field('hospital')
        except Exception:
            continue
        if getattr(field, 'is_relation', False) and field.related_model \
                and field.related_model._meta.label == 'saas.Hospital':
            out.append(model)
    return out


def purge_tenant(hospital):
    """Delete every hospital-scoped row for `hospital` (not the hospital row or
    its users). Safe to call inside an outer transaction. Returns the number of
    models still non-empty (0 = fully cleared)."""
    models = _hospital_scoped_models()
    remaining = 0
    for _ in range(_MAX_PASSES):
        progressed = False
        remaining = 0
        for model in models:
            mgr = getattr(model, 'all_objects', None) or model._base_manager
            qs = mgr.filter(hospital=hospital)
            if not qs.exists():
                continue
            try:
                # Savepoint so a ProtectedError rolls back just this delete and
                # leaves the outer transaction usable for the next model/pass.
                with transaction.atomic():
                    qs.delete()
                progressed = True
            except ProtectedError:
                remaining += 1
        if remaining == 0 or not progressed:
            break
    return remaining
