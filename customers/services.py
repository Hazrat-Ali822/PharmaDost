from decimal import Decimal
from django.db import transaction
from .models import Customer, CustomerPayment


@transaction.atomic
def record_payment(customer, amount, method="CASH", notes="", received_by=None, linked_sale=None, date=None):
    """Record a payment received from a customer and reduce their outstanding balance."""
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("Payment amount must be positive")

    cust = Customer.objects.select_for_update().get(pk=getattr(customer, "pk", customer))

    kwargs = dict(
        customer=cust,
        amount=amount,
        method=method,
        notes=notes,
        received_by=received_by,
        linked_sale=linked_sale,
    )
    if date:
        kwargs["date"] = date
    payment = CustomerPayment.objects.create(**kwargs)

    cust.balance -= amount
    cust.save(update_fields=["balance"])
    return payment
