"""Panel ledger operations.

`record_payment` is the one write path for money coming in from a panel; it also
FIFO-allocates the payment across the panel's open claims (Phase 3). Coverage
limits (Phase 2) are applied at billing time by `apply_coverage`, which caps what
the panel owes for a patient and pushes any excess onto the patient. Outstanding
is always computed from invoices + sales minus payments, so there is no stored
balance to keep in sync.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import Panel, PanelPayment

ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Outstanding (panel-level)
# ---------------------------------------------------------------------------
def outstanding_for(panel):
    """What this one panel still owes: billed − collected-at-counter − panel paid.
    Counts both service invoices and pharmacy (POS) sales billed to the panel."""
    from billing.models import Invoice
    from sales.models import Sale
    inv = (Invoice.objects.filter(panel=panel)
           .aggregate(billed=Sum('total'), copay=Sum('paid')))
    sal = (Sale.objects.filter(panel=panel, is_returned=False)
           .aggregate(billed=Sum('total'), copay=Sum('paid')))
    paid = panel.payments.aggregate(s=Sum('amount'))['s'] or ZERO
    billed = (inv['billed'] or ZERO) + (sal['billed'] or ZERO)
    copay = (inv['copay'] or ZERO) + (sal['copay'] or ZERO)
    return billed - copay - paid


def outstanding_map(panels):
    """Outstanding per panel in a fixed number of queries (no per-panel query in
    a loop). Counts service invoices AND pharmacy sales. Returns {panel_id: Decimal}."""
    from billing.models import Invoice
    from sales.models import Sale
    ids = [p.pk for p in panels]

    def _grouped(qs, field):
        return {r['panel_id']: (r['v'] or ZERO)
                for r in qs.filter(panel_id__in=ids).values('panel_id').annotate(v=Sum(field))}

    inv_billed = _grouped(Invoice.objects, 'total')
    inv_copay = _grouped(Invoice.objects, 'paid')
    sal_billed = _grouped(Sale.objects.filter(is_returned=False), 'total')
    sal_copay = _grouped(Sale.objects.filter(is_returned=False), 'paid')
    paid = _grouped(PanelPayment.objects, 'amount')

    def owed(pid):
        billed = inv_billed.get(pid, ZERO) + sal_billed.get(pid, ZERO)
        copay = inv_copay.get(pid, ZERO) + sal_copay.get(pid, ZERO)
        return billed - copay - paid.get(pid, ZERO)

    return {pid: owed(pid) for pid in ids}


# ---------------------------------------------------------------------------
# Phase 2 — coverage limits (per patient)
# ---------------------------------------------------------------------------
def coverage_used(patient):
    """How much the panel has already been put on the hook for, for THIS patient —
    the panel-owed portion (total − co-pay) of their panel invoices and sales. A
    panel paying later does not restore the patient's limit, so payments are not
    subtracted here."""
    from billing.models import Invoice
    from sales.models import Sale
    inv = (Invoice.objects.filter(patient=patient, panel__isnull=False)
           .aggregate(t=Sum('total'), p=Sum('paid')))
    sal = (Sale.objects.filter(patient=patient, panel__isnull=False, is_returned=False)
           .aggregate(t=Sum('total'), p=Sum('paid')))
    used = ((inv['t'] or ZERO) - (inv['p'] or ZERO)) + ((sal['t'] or ZERO) - (sal['p'] or ZERO))
    return used


def coverage_remaining(patient):
    """Remaining coverage for the patient, or None when the limit is unlimited (0)."""
    limit = getattr(patient, 'panel_coverage_limit', ZERO) or ZERO
    if limit <= 0:
        return None
    return limit - coverage_used(patient)


def apply_coverage(patient, total, panel):
    """Given a new bill of `total` about to be billed to `panel`, apply the
    patient's coverage limit. Returns (effective_panel, patient_pays):

      * (None, None)            → don't attribute to the panel (no panel, or the
                                   limit is exhausted) — the caller bills normally.
      * (panel, Decimal('0'))   → fully covered; the panel owes it all.
      * (panel, excess)         → partially covered; the panel owes the remaining
                                   coverage, the patient pays `excess`.
    """
    if panel is None or patient is None:
        return (panel, ZERO) if panel is not None else (None, None)
    remaining = coverage_remaining(patient)
    if remaining is None:            # unlimited
        return (panel, ZERO)
    if remaining <= 0:               # exhausted → patient pays, no panel
        return (None, None)
    if total <= remaining:
        return (panel, ZERO)
    return (panel, total - remaining)


# ---------------------------------------------------------------------------
# Phase 3 — payments + FIFO allocation across claims
# ---------------------------------------------------------------------------
def _open_claims(panel):
    """Panel's unsettled claims (invoices + sales), oldest first, each with its
    remaining panel-owed amount."""
    from billing.models import Invoice
    from sales.models import Sale
    rows = []
    for inv in Invoice.objects.filter(panel=panel):
        owed = (inv.total or ZERO) - (inv.paid or ZERO) - (inv.panel_settled or ZERO)
        if owed > 0:
            rows.append((inv.created_at, 'invoice', inv, owed))
    for s in Sale.objects.filter(panel=panel, is_returned=False):
        owed = (s.total or ZERO) - (s.paid or ZERO) - (s.panel_settled or ZERO)
        if owed > 0:
            rows.append((s.created_at, 'sale', s, owed))
    # Oldest first; break ties on pk so allocation is deterministic when several
    # claims share a timestamp (common in tests and fast bulk entry).
    rows.sort(key=lambda r: (r[0], r[2].pk))
    return rows


def allocate_payment(panel, amount, prefer_invoice=None):
    """Spread `amount` across the panel's open claims, oldest first (a specific
    invoice first if given). Marks each fully-settled invoice claim PAID. Returns
    the total allocated (may be < amount if the panel overpaid)."""
    amount = Decimal(str(amount))
    claims = _open_claims(panel)
    if prefer_invoice is not None:
        claims.sort(key=lambda r: (r[1] != 'invoice' or r[2].pk != prefer_invoice.pk))
    allocated = ZERO
    for _when, kind, obj, owed in claims:
        if amount <= 0:
            break
        take = min(amount, owed)
        obj.panel_settled = (obj.panel_settled or ZERO) + take
        fields = ['panel_settled']
        if kind == 'invoice' and obj.panel_settled >= (obj.total - obj.paid):
            obj.claim_status = 'PAID'
            fields.append('claim_status')
        obj.save(update_fields=fields)
        amount -= take
        allocated += take
    return allocated


@transaction.atomic
def record_payment(panel, amount, method="BANK", reference="", notes="",
                   received_by=None, linked_invoice=None, date=None):
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    payment = PanelPayment(
        panel=panel, amount=amount, method=method, reference=reference,
        notes=notes, received_by=received_by, linked_invoice=linked_invoice,
    )
    if date:
        payment.date = date
    payment.save()
    allocate_payment(panel, amount, prefer_invoice=linked_invoice)
    return payment
