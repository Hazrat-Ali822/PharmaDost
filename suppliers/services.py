from decimal import Decimal
from django.db import transaction
from .models import Supplier, SupplierPayment


@transaction.atomic
def record_supplier_payment(supplier, amount, method="CASH", notes="", by_user=None, date=None):
    """Record a payment made to a supplier and reduce our payable balance."""
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")

    sup = Supplier.objects.select_for_update().get(pk=getattr(supplier, "pk", supplier))

    kwargs = dict(supplier=sup, amount=amount, method=method, notes=notes, by_user=by_user)
    if date:
        kwargs["date"] = date
    payment = SupplierPayment.objects.create(**kwargs)

    sup.balance -= amount
    sup.save(update_fields=["balance"])
    return payment
