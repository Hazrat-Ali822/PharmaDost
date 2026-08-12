from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Invoice, InvoiceItem, Expense, PatientPayment


def next_invoice_number(hospital):
    """Reserve and return the next invoice number for this hospital.

    Reuses the same locked-SiteSettings-row pattern as the MRN counter so two
    invoices raised at the same instant cannot share a number. With the year
    switched on, the running count restarts at 1 each calendar year.
    """
    from patients.services import _settings_row

    with transaction.atomic():
        row = _settings_row(hospital, lock=True)
        prefix = (row.invoice_prefix or 'INV').upper()
        if row.invoice_year_in_number:
            year = timezone.localdate().year
            if row.invoice_number_year != year:
                row.invoice_number_year = year
                row.invoice_last_number = 0
            row.invoice_last_number = (row.invoice_last_number or 0) + 1
            row.save(update_fields=['invoice_last_number', 'invoice_number_year'])
            return f"{prefix}-{year}-{row.invoice_last_number:05d}"
        row.invoice_last_number = (row.invoice_last_number or 0) + 1
        row.save(update_fields=['invoice_last_number'])
        return f"{prefix}-{row.invoice_last_number:05d}"


def patient_billing_summary(patient):
    """Everything the patient has been charged, across service/OPD invoices and
    pharmacy sales, plus the running totals."""
    invoices = list(patient.invoices.prefetch_related('items').order_by('created_at'))
    sales = list(patient.pharmacy_sales.filter(is_returned=False)
                 .prefetch_related('items', 'items__medicine').order_by('created_at'))

    inv_charged = sum((i.total for i in invoices), Decimal('0.00'))
    inv_paid = sum((i.paid for i in invoices), Decimal('0.00'))
    sale_charged = sum((s.total for s in sales), Decimal('0.00'))
    sale_paid = sum((s.paid for s in sales), Decimal('0.00'))

    charged = inv_charged + sale_charged
    paid = inv_paid + sale_paid
    return {
        'invoices': invoices,
        'sales': sales,
        'charged': charged,
        'paid': paid,
        'outstanding': charged - paid,
        'payments': list(patient.payments.select_related('collected_by').all()),
    }


def patient_totals(patient):
    """Light charged/paid/outstanding for one patient (invoices + non-returned sales)."""
    inv = patient.invoices.aggregate(
        c=Coalesce(Sum('total'), Decimal('0.00')), p=Coalesce(Sum('paid'), Decimal('0.00')))
    sal = patient.pharmacy_sales.filter(is_returned=False).aggregate(
        c=Coalesce(Sum('total'), Decimal('0.00')), p=Coalesce(Sum('paid'), Decimal('0.00')))
    charged = inv['c'] + sal['c']
    paid = inv['p'] + sal['p']
    return {'charged': charged, 'paid': paid, 'outstanding': charged - paid}


def outstanding_by_patient():
    """All patients who currently owe money, biggest balance first — for the billing desk."""
    from patients.models import Patient
    from sales.models import Sale

    rows = {}

    def add(pid, c, p):
        if not pid:
            return
        acc = rows.setdefault(pid, [Decimal('0.00'), Decimal('0.00')])
        acc[0] += c
        acc[1] += p

    for r in Invoice.objects.values('patient').annotate(
            c=Coalesce(Sum('total'), Decimal('0.00')), p=Coalesce(Sum('paid'), Decimal('0.00'))):
        add(r['patient'], r['c'], r['p'])
    for r in Sale.objects.filter(is_returned=False).values('patient').annotate(
            c=Coalesce(Sum('total'), Decimal('0.00')), p=Coalesce(Sum('paid'), Decimal('0.00'))):
        add(r['patient'], r['c'], r['p'])

    pmap = {p.id: p for p in Patient.objects.filter(id__in=rows.keys())}
    out = []
    for pid, (c, p) in rows.items():
        if c - p > 0 and pid in pmap:
            out.append({'patient': pmap[pid], 'charged': c, 'paid': p, 'outstanding': c - p})
    out.sort(key=lambda d: d['outstanding'], reverse=True)
    return out


def _outstanding_items(patient):
    """Unpaid invoices + credit sales, oldest first."""
    items = []
    for inv in patient.invoices.all():
        if inv.balance > 0:
            items.append((inv.created_at, inv))
    for sale in patient.pharmacy_sales.filter(is_returned=False):
        if sale.balance > 0:
            items.append((sale.created_at, sale))
    items.sort(key=lambda t: t[0])
    return [obj for _, obj in items]


def collect_patient_payment(*, patient, amount, method, user, note=''):
    """Record a payment and allocate it across the patient's outstanding items
    (oldest first). Any excess beyond the outstanding balance stays recorded on
    the PatientPayment as an advance but isn't allocated."""
    amount = Decimal(str(amount))
    payment = PatientPayment.objects.create(
        patient=patient, amount=amount, payment_method=method,
        collected_by=user, note=note)

    remaining = amount
    for obj in _outstanding_items(patient):
        if remaining <= 0:
            break
        pay = min(remaining, obj.balance)
        obj.paid = (obj.paid or Decimal('0.00')) + pay
        obj.save(update_fields=['paid'])
        remaining -= pay
    return payment


def cash_position(day):
    """Compute the CASH drawer movement for a single day.

    cash_in  = cash sales collected + cash invoice collections
    cash_out = cash expenses + cash doctor payouts
    Returns a dict (opening excluded — supplied at closing time).
    """
    from sales.models import Sale
    from opd.models import DoctorPayout

    sales_cash = (Sale.objects
                  .filter(created_at__date=day, is_returned=False, payment_method='CASH')
                  .aggregate(t=Coalesce(Sum('paid'), Decimal('0.00')))['t'])
    inv_cash = (Invoice.objects
                .filter(created_at__date=day, payment_method='CASH')
                .aggregate(t=Coalesce(Sum('paid'), Decimal('0.00')))['t'])
    exp_cash = (Expense.objects
                .filter(date=day, payment_method='CASH')
                .aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t'])
    payout_cash = (DoctorPayout.objects
                   .filter(date=day, payment_method='CASH')
                   .aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t'])

    cash_in = sales_cash + inv_cash
    cash_out = exp_cash + payout_cash
    return {
        'sales_cash': sales_cash,
        'inv_cash': inv_cash,
        'exp_cash': exp_cash,
        'payout_cash': payout_cash,
        'cash_in': cash_in,
        'cash_out': cash_out,
        'net': cash_in - cash_out,
    }


def _rederive_totals(invoice):
    """Recompute subtotal/discount/tax/total from whatever lines the invoice still has,
    using the same SiteSettings maths `create_service_invoice` built it with."""
    from user_mgmt.models import SiteSettings
    site = SiteSettings.load()
    subtotal = invoice.items.aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t']
    # A standing rupee discount larger than what is left would make the bill negative.
    discount = min(invoice.discount or Decimal('0.00'), subtotal)
    after_discount = subtotal - discount
    tax = site.tax_on(after_discount)
    invoice.subtotal = subtotal
    invoice.discount = discount
    invoice.tax = tax
    invoice.total = site.round_total(after_discount + tax)


def cancel_invoice_charge(invoice, description):
    """Drop one chargeable line off an ACTIVE invoice and re-derive its totals.

    Called when a patient refuses a service that was already ordered (a lab test,
    a scan). The order is cancelled, so the money has to come off the bill with it —
    a 3-test invoice becomes a 2-test invoice instead of needing the whole thing
    voided, which is what `invoice_void` would otherwise force. When the last line
    goes the invoice is VOIDed, since a bill for nothing is not a bill.

    **`paid` is never lowered.** If the patient already handed over more than the new
    total, the excess comes back as `refund_due` for the desk to return in cash;
    quietly reducing `paid` would erase money the day book has already counted.

    A free service (price 0) never got an `InvoiceItem` in the first place, so
    `removed` is False and nothing happens — which is correct, not a failure.
    """
    result = {'removed': False, 'voided': False, 'refund_due': Decimal('0.00')}
    if invoice is None or invoice.status != 'ACTIVE':
        return result

    with transaction.atomic():
        # `_base_manager`, not `objects`/`all_objects`: both are TenantManagers and
        # would re-scope by the thread-local, which fails on a legacy invoice whose
        # `hospital` is NULL. The caller already reached this invoice through a
        # tenant-scoped order/study, so the authorisation is done — this is only a
        # lock on a row we are permitted to touch, in whatever state it is in.
        inv = Invoice._base_manager.select_for_update().get(pk=invoice.pk)
        line = inv.items.filter(description=description).first()
        if line is None:
            return result
        line.delete()
        result['removed'] = True

        _rederive_totals(inv)
        if not inv.items.exists():
            inv.status = 'VOID'
            result['voided'] = True
        inv.save(update_fields=['subtotal', 'discount', 'tax', 'total', 'status'])

        paid = inv.paid or Decimal('0.00')
        if paid > inv.total:
            result['refund_due'] = paid - inv.total

        # keep the caller's in-memory copy from going stale mid-request
        for f in ('subtotal', 'discount', 'tax', 'total', 'status'):
            setattr(invoice, f, getattr(inv, f))
    return result


def create_service_invoice(*, patient, items, created_by, paid=Decimal('0.00'),
                           payment_method='CASH', discount=Decimal('0.00'),
                           appointment=None, panel=None, service=None):
    """Create an invoice for one or more chargeable services (lab tests, imaging
    scans, procedures...). `items` is a list of (description, amount) tuples.

    Defaults to paid=0 (a pending payable) so reception / accounts can collect it.
    Returns None if there is nothing to charge.

    If the patient is covered by a panel (or one is passed), the bill is filed as
    a claim against it — the unpaid balance becomes the panel's receivable. `paid`
    then represents any co-pay collected from the patient at the counter.
    """
    items = [(d, Decimal(str(a))) for d, a in items if a and Decimal(str(a)) > 0]
    if not items:
        return None
    from user_mgmt.models import SiteSettings
    site = SiteSettings.load()
    subtotal = sum((amt for _, amt in items), Decimal('0.00'))
    after_discount = subtotal - discount
    tax = site.tax_on(after_discount)
    total = site.round_total(after_discount + tax)
    panel = panel or getattr(patient, 'panel', None)
    # Coverage limit: the panel owes at most the patient's remaining coverage; any
    # excess is the patient's. `floor` is the minimum the patient must pay (0 when
    # fully covered/unlimited) — it never lowers a co-pay the caller already set.
    # An exhausted limit drops the panel (floor None).
    from panels.services import apply_coverage
    panel, floor = apply_coverage(patient, total, panel, service=service)
    if panel is not None:
        paid = max(paid, floor)
    invoice = Invoice.objects.create(
        patient=patient,
        appointment=appointment,
        subtotal=subtotal,
        discount=discount,
        tax=tax,
        total=total,
        paid=paid,
        payment_method=payment_method,
        created_by=created_by,
        panel=panel,
        claim_status='PENDING' if panel else '',
    )
    for desc, amt in items:
        InvoiceItem.objects.create(invoice=invoice, description=desc, amount=amt)
    return invoice


def create_opd_invoice(appointment, created_by, payment_method='CASH', discount=Decimal('0.00'), panel=None):
    from user_mgmt.models import SiteSettings
    site = SiteSettings.load()
    fee = appointment.doctor.opd_fee if appointment.visit_type != 'FOLLOWUP' else appointment.doctor.followup_fee
    after_discount = fee - discount
    tax = site.tax_on(after_discount)
    total = site.round_total(after_discount + tax)
    # A covered patient's consultation is owed by the panel (a claim); an uncovered
    # patient — or one whose coverage is exhausted — pays the OPD fee upfront.
    # Under a coverage limit the panel owes up to the remaining cover, the patient
    # pays any excess upfront.
    panel = panel or getattr(appointment.patient, 'panel', None)
    from panels.services import apply_coverage
    panel, patient_pays = apply_coverage(appointment.patient, total, panel, service='OPD')
    invoice = Invoice.objects.create(
        patient=appointment.patient,
        appointment=appointment,
        subtotal=fee,
        discount=discount,
        tax=tax,
        total=total,
        paid=(patient_pays if panel is not None else total),
        payment_method=payment_method,
        created_by=created_by,
        panel=panel,
        claim_status='PENDING' if panel else '',
    )
    InvoiceItem.objects.create(invoice=invoice, description='OPD Consultation', amount=fee)
    return invoice
