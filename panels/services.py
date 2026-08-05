"""Panel ledger operations.

`record_payment` is the one write path for money coming in from a panel; the
online view and (later) the offline replay both call it. Outstanding is always
computed from invoices minus payments (`outstanding_for` / `outstanding_map`),
so there is no stored balance to keep in sync.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import Panel, PanelPayment


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
    return payment


def outstanding_for(panel):
    """What this one panel still owes: billed − collected-at-counter − panel paid."""
    from billing.models import Invoice
    inv = (Invoice.objects.filter(panel=panel)
           .aggregate(billed=Sum('total'), copay=Sum('paid')))
    paid = panel.payments.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    billed = inv['billed'] or Decimal('0.00')
    copay = inv['copay'] or Decimal('0.00')
    return billed - copay - paid


def outstanding_map(panels):
    """Outstanding per panel in a fixed number of queries (no per-panel query in
    a loop) — the same grouped-aggregate shape the SaaS portal uses. Returns
    {panel_id: Decimal}."""
    from billing.models import Invoice
    ids = [p.pk for p in panels]
    billed = {r['panel_id']: (r['b'] or Decimal('0.00'))
              for r in Invoice.objects.filter(panel_id__in=ids)
              .values('panel_id').annotate(b=Sum('total'))}
    copay = {r['panel_id']: (r['c'] or Decimal('0.00'))
             for r in Invoice.objects.filter(panel_id__in=ids)
             .values('panel_id').annotate(c=Sum('paid'))}
    paid = {r['panel_id']: (r['p'] or Decimal('0.00'))
            for r in PanelPayment.objects.filter(panel_id__in=ids)
            .values('panel_id').annotate(p=Sum('amount'))}
    return {pid: billed.get(pid, Decimal('0.00')) - copay.get(pid, Decimal('0.00'))
            - paid.get(pid, Decimal('0.00')) for pid in ids}
