from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from inventory.models import Medicine, StockBatch
from .models import Sale, SaleItem


def _dec(value, default="0.00"):
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


@transaction.atomic
def create_sale(*, items, sale_type=Sale.RETAIL, customer=None, customer_name="",
                discount=0, paid=None, payment_method="CASH", cashier=None, patient=None):
    """
    Create a sale, dispensing stock FEFO (earliest expiry first) and recording the
    exact batch each line came from.

    items: list of {"medicine_id": int, "quantity": int,
                    optional "unit_price": Decimal, optional "discount": Decimal}
    """
    if not items:
        raise ValueError("Add at least one item.")

    # Wholesale pricing is ONLY for registered customers, and only on a wholesale sale.
    if sale_type == Sale.WHOLESALE and customer is None:
        raise ValueError("Wholesale price is only for registered customers — please select a customer.")

    order_discount = _dec(discount)

    if customer is not None:
        # lock the customer row for balance/credit-limit consistency
        from customers.models import Customer
        customer = Customer.objects.select_for_update().get(pk=customer.pk)
        if not customer_name:
            customer_name = customer.shop_name or customer.name

    sale = Sale.objects.create(
        sale_type=sale_type,
        customer=customer,
        patient=patient,
        customer_name=customer_name,
        payment_method=payment_method,
        cashier=cashier,
        discount=order_discount,
    )

    gross = Decimal("0.00")
    total_line_discount = Decimal("0.00")

    for it in items:
        med = Medicine.objects.select_for_update().get(id=it["medicine_id"])
        qty = int(it["quantity"])
        if qty < 1:
            raise ValueError("Quantity must be at least 1.")
        if med.is_expired:
            raise ValueError(f"{med.name} is expired - cannot sell.")
        if med.quantity < qty:
            raise ValueError(f"Not enough stock for {med.name}.")

        if it.get("unit_price") not in (None, ""):
            unit_price = _dec(it["unit_price"])
        elif sale_type == Sale.WHOLESALE and med.wholesale_price:
            unit_price = med.wholesale_price
        else:
            unit_price = med.price
        line_discount = _dec(it.get("discount"))

        # FEFO dispense; returns one chunk per batch consumed
        consumed = med.reduce_stock(qty)

        first = True
        for chunk in consumed:
            batch = None
            if chunk.get("batch_id"):
                batch = StockBatch.objects.get(id=chunk["batch_id"])
            chunk_qty = chunk["quantity"]
            # attach the whole line discount to the first chunk so line_total sums correctly
            item_disc = line_discount if first else Decimal("0.00")
            SaleItem.objects.create(
                sale=sale,
                medicine=med,
                batch=batch,
                unit_price=unit_price,
                quantity=chunk_qty,
                discount=item_disc,
            )
            gross += unit_price * chunk_qty
            first = False

        total_line_discount += line_discount

    subtotal = gross
    total = subtotal - total_line_discount - order_discount
    if total < 0:
        raise ValueError("Discount cannot exceed the sale total.")

    paid_amount = total if paid in (None, "") else _dec(paid)
    if paid_amount < 0:
        raise ValueError("Paid amount cannot be negative.")

    sale.subtotal = subtotal
    sale.total = total
    sale.paid = paid_amount

    credit_amount = total - paid_amount
    if credit_amount > 0:
        if customer is None:
            raise ValueError("Credit / partial-payment sale requires a customer.")
        new_balance = customer.balance + credit_amount
        if customer.credit_limit and new_balance > customer.credit_limit:
            raise ValueError(
                f"Credit limit exceeded. Limit {customer.credit_limit}, would become {new_balance}."
            )
        customer.balance = new_balance
        customer.save(update_fields=["balance"])
        if payment_method != "CREDIT":
            sale.payment_method = "CREDIT"

    sale.save(update_fields=["subtotal", "total", "paid", "payment_method"])
    return sale


@transaction.atomic
def return_sale(sale, by_user=None):
    """Reverse a sale: return stock to its batches and undo any credit balance."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.is_returned:
        raise ValueError("Sale already returned.")

    for item in sale.items.select_related("medicine", "batch"):
        med = Medicine.objects.select_for_update().get(pk=item.medicine_id)
        if item.batch_id:
            batch = StockBatch.objects.select_for_update().get(pk=item.batch_id)
            batch.quantity += item.quantity
            batch.save(update_fields=["quantity"])
        med.quantity += item.quantity
        med.save(update_fields=["quantity"])

    credit_amount = sale.total - sale.paid
    if sale.customer_id and credit_amount > 0:
        from customers.models import Customer
        cust = Customer.objects.select_for_update().get(pk=sale.customer_id)
        cust.balance -= credit_amount
        cust.save(update_fields=["balance"])

    sale.is_returned = True
    sale.save(update_fields=["is_returned"])
    return sale


# Simple report helpers

def date_range_for(period: str):
    today = date.today()
    if period == "daily":
        start = today
    elif period == "weekly":
        start = today - timedelta(days=today.weekday())
    elif period == "monthly":
        start = today.replace(day=1)
    else:
        raise ValueError("period must be daily, weekly, or monthly")
    return start, today + timedelta(days=1)
