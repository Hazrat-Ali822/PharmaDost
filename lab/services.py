"""Lab operations that must behave identically whether they were typed at the
desk or replayed from a device that was offline.

`lab.views.order_create` and `offline_sync.handlers.handle_lab` both call
`create_test_order`, so there is exactly one place that decides an order also
raises its pending bill — the offline path can never drift into a looser one.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


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


def _notify_orderer(order, message):
    """Tell the doctor who asked for the test that it will not be coming back.

    Not `notify_admins` — this is not an exception the owner must police, it is a
    clinical fact one specific person needs. A doctor who never learns their test
    was cancelled waits for a result that will never arrive.
    """
    from accounts.models import Notification

    user = order.ordered_by
    if user and user.is_active:
        Notification.objects.create(
            user=user, message=message, link=f"/lab/orders/{order.id}/")


def _mark_order_cancelled(order, *, user, reason):
    order.status = 'Cancelled'
    order.cancelled_at = timezone.now()
    order.cancelled_by = user
    order.cancel_reason = reason
    order.save(update_fields=['status', 'cancelled_at', 'cancelled_by', 'cancel_reason'])


def cancel_test(result, *, user, reason):
    """Cancel one test off an order — the patient refused it, or the doctor withdrew it.

    Soft, never a delete: the row keeps `is_cancelled` + who + when + why, so the
    report and the patient's history can still answer "what happened to that test".
    The matching line comes off the invoice (`cancel_invoice_charge`), so a 3-test
    bill becomes a 2-test bill. When the last live test goes, the order itself is
    cancelled and its now-empty invoice is voided.

    A test that already has a **result entered** cannot be cancelled: the lab has
    done the work and used the reagent, so that is a billing decision (void/refund
    from Billing), not a lab one. Returns the `cancel_invoice_charge` dict.
    """
    from billing.services import cancel_invoice_charge

    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("Please say why the test is being cancelled.")
    if result.is_cancelled:
        raise ValidationError("That test is already cancelled.")
    if (result.result_value or '').strip():
        raise ValidationError(
            f"{result.lab_test.name} already has a result entered — the lab has done "
            f"the work. If it must not be charged, void the bill from Billing instead.")

    order = result.test_order
    if order.is_cancelled:
        raise ValidationError("This whole order is already cancelled.")

    with transaction.atomic():
        result.is_cancelled = True
        result.cancelled_at = timezone.now()
        result.cancelled_by = user
        result.cancel_reason = reason
        result.save(update_fields=['is_cancelled', 'cancelled_at',
                                   'cancelled_by', 'cancel_reason'])
        money = cancel_invoice_charge(order.invoice, f"Lab: {result.lab_test.name}")
        if not order.results.filter(is_cancelled=False).exists():
            _mark_order_cancelled(order, user=user, reason=reason)

    if user != order.ordered_by:
        _notify_orderer(
            order,
            f"🚫 Lab test cancelled — {result.lab_test.name} for "
            f"{order.patient.full_name} (order #{order.id}): {reason}")
    return money


def cancel_order(order, *, user, reason):
    """Cancel every test still outstanding on an order, and void its bill.

    Refuses outright if *any* live test already has a result — cancel those
    individually so the operator has to look at each one, rather than wiping a
    part-finished order in a single click.
    """
    from billing.services import cancel_invoice_charge

    reason = (reason or '').strip()
    if not reason:
        raise ValidationError("Please say why the order is being cancelled.")
    if order.is_cancelled:
        raise ValidationError("This order is already cancelled.")

    live = list(order.results.filter(is_cancelled=False).select_related('lab_test'))
    done = [r.lab_test.name for r in live if (r.result_value or '').strip()]
    if done:
        raise ValidationError(
            "Results are already entered for " + ", ".join(done) +
            " — the lab has done that work. Cancel the remaining tests one by one.")

    money = {'removed': False, 'voided': False, 'refund_due': 0}
    with transaction.atomic():
        for r in live:
            r.is_cancelled = True
            r.cancelled_at = timezone.now()
            r.cancelled_by = user
            r.cancel_reason = reason
            r.save(update_fields=['is_cancelled', 'cancelled_at',
                                  'cancelled_by', 'cancel_reason'])
            m = cancel_invoice_charge(order.invoice, f"Lab: {r.lab_test.name}")
            money['removed'] = money['removed'] or m['removed']
            money['voided'] = money['voided'] or m['voided']
            money['refund_due'] = m['refund_due'] or money['refund_due']
        _mark_order_cancelled(order, user=user, reason=reason)

    if user != order.ordered_by:
        _notify_orderer(
            order,
            f"🚫 Lab order #{order.id} cancelled for {order.patient.full_name} "
            f"({len(live)} test(s)): {reason}")
    return money
