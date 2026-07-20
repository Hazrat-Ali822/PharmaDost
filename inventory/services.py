from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Medicine, StockBatch, StockAdjustment, PurchaseReturn, PurchaseReturnItem


def sales_velocity(days=30):
    """{medicine_id: units_sold} over the last `days` days (non-returned sales only)."""
    since = timezone.now() - timedelta(days=days)
    from sales.models import SaleItem
    rows = (SaleItem.objects
            .filter(sale__created_at__gte=since, sale__is_returned=False)
            .values('medicine_id')
            .annotate(qty=Sum('quantity')))
    return {r['medicine_id']: (r['qty'] or 0) for r in rows}


def reorder_suggestions(*, days=30, lead_days=7, safety_days=5, cover_days=30):
    """Suggest a DYNAMIC reorder point + order quantity per active medicine from
    recent sales velocity (vs the static reorder_level).

        reorder point = avg daily usage x (lead time + safety days)
        order qty      = avg daily usage x cover_days  -  in-date stock on hand

    Rows that are at/below their reorder point (or already low) come first."""
    vel = sales_velocity(days)
    out = []
    for med in Medicine.objects.all():
        sold = vel.get(med.id, 0)
        adu = sold / days if days else 0                     # average daily usage
        suggested_level = int(round(adu * (lead_days + safety_days)))
        on_hand = med.sellable_quantity
        suggested_order = max(0, int(round(adu * cover_days)) - on_hand)
        needs = on_hand <= suggested_level or med.is_low_stock
        out.append({
            'medicine': med, 'sold': sold, 'adu': round(adu, 2),
            'on_hand': on_hand, 'reorder_level': med.reorder_level,
            'suggested_level': suggested_level, 'suggested_order': suggested_order,
            'needs': needs,
        })
    out.sort(key=lambda r: (not r['needs'], -r['suggested_order']))
    return out


@transaction.atomic
def apply_adjustment(*, batch, qty_change, reason, notes="", by_user=None):
    """
    Adjust a batch's stock by qty_change (+/-) and log it.
    Updates both the batch and the aggregate Medicine.quantity.
    """
    qty_change = int(qty_change)
    if qty_change == 0:
        raise ValueError("Quantity change cannot be zero.")

    batch = StockBatch.objects.select_for_update().get(pk=getattr(batch, "pk", batch))
    new_batch_qty = batch.quantity + qty_change
    if new_batch_qty < 0:
        raise ValueError(f"Cannot remove {abs(qty_change)} — batch only has {batch.quantity} in stock.")

    med = Medicine.all_objects.select_for_update().get(pk=batch.medicine_id)
    new_med_qty = med.quantity + qty_change
    if new_med_qty < 0:
        new_med_qty = 0  # guard against legacy drift

    batch.quantity = new_batch_qty
    batch.save(update_fields=["quantity"])
    med.quantity = new_med_qty
    med.save(update_fields=["quantity"])

    return StockAdjustment.objects.create(
        batch=batch, qty_change=qty_change, reason=reason, notes=notes, by_user=by_user
    )


@transaction.atomic
def create_purchase_return(*, supplier=None, reason="EXPIRY", notes="", items, by_user=None):
    """
    Return purchased goods to a supplier. Reduces batch + medicine stock.
    items: list of {"batch_id": int, "quantity": int, optional "cost_price": Decimal}
    """
    if not items:
        raise ValueError("Add at least one item to return.")

    ret = PurchaseReturn.objects.create(
        supplier=supplier, reason=reason, notes=notes, created_by=by_user
    )

    total = Decimal("0.00")
    for it in items:
        qty = int(it["quantity"])
        if qty < 1:
            raise ValueError("Return quantity must be at least 1.")
        batch = StockBatch.objects.select_for_update().get(pk=it["batch_id"])
        if batch.quantity < qty:
            raise ValueError(f"{batch} only has {batch.quantity} in stock — cannot return {qty}.")

        cost = it.get("cost_price")
        cost = batch.cost_price if cost in (None, "") else Decimal(str(cost))

        med = Medicine.all_objects.select_for_update().get(pk=batch.medicine_id)
        batch.quantity -= qty
        batch.save(update_fields=["quantity"])
        med.quantity = max(0, med.quantity - qty)
        med.save(update_fields=["quantity"])

        PurchaseReturnItem.objects.create(ret=ret, batch=batch, quantity=qty, cost_price=cost)
        total += cost * qty

    ret.total = total
    ret.save(update_fields=["total"])

    # returning goods reduces what we owe the supplier
    if supplier is not None and total:
        from suppliers.models import Supplier
        sup = Supplier.objects.select_for_update().get(pk=supplier.pk)
        sup.balance -= total
        sup.save(update_fields=["balance"])

    return ret
