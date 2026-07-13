from decimal import Decimal
from django.db import transaction

from .models import Medicine, StockBatch, StockAdjustment, PurchaseReturn, PurchaseReturnItem


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
