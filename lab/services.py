"""Lab operations that must behave identically whether they were typed at the
desk or replayed from a device that was offline.

`lab.views.order_create` and `offline_sync.handlers.handle_lab` both call
`create_test_order`, so there is exactly one place that decides an order also
raises its pending bill — the offline path can never drift into a looser one.
"""
from django.db import transaction


def create_test_order(form, user):
    """Save a validated `TestOrderCreateForm` and raise the pending bill for it.

    The order and its invoice are one unit of work: a test sent to the lab with
    no bill behind it is money the desk never collects.
    """
    from billing.services import create_service_invoice

    with transaction.atomic():
        order = form.save()
        items = [(f"Lab: {r.lab_test.name}", r.lab_test.price)
                 for r in order.results.select_related("lab_test")]
        invoice = create_service_invoice(
            patient=order.patient, items=items, created_by=user, service='LAB')
        if invoice:
            order.invoice = invoice
            order.save()
    return order
