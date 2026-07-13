from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

from .models import Invoice, InvoiceItem, Expense, PatientPayment


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


def create_service_invoice(*, patient, items, created_by, paid=Decimal('0.00'),
                           payment_method='CASH', discount=Decimal('0.00'),
                           appointment=None):
    """Create an invoice for one or more chargeable services (lab tests, imaging
    scans, procedures...). `items` is a list of (description, amount) tuples.

    Defaults to paid=0 (a pending payable) so reception / accounts can collect it.
    Returns None if there is nothing to charge.
    """
    items = [(d, Decimal(str(a))) for d, a in items if a and Decimal(str(a)) > 0]
    if not items:
        return None
    subtotal = sum((amt for _, amt in items), Decimal('0.00'))
    total = subtotal - discount
    invoice = Invoice.objects.create(
        patient=patient,
        appointment=appointment,
        subtotal=subtotal,
        discount=discount,
        total=total,
        paid=paid,
        payment_method=payment_method,
        created_by=created_by,
    )
    for desc, amt in items:
        InvoiceItem.objects.create(invoice=invoice, description=desc, amount=amt)
    return invoice


def create_opd_invoice(appointment, created_by, payment_method='CASH', discount=Decimal('0.00')):
    fee = appointment.doctor.opd_fee if appointment.visit_type != 'FOLLOWUP' else appointment.doctor.followup_fee
    invoice = Invoice.objects.create(
        patient=appointment.patient,
        appointment=appointment,
        subtotal=fee,
        discount=discount,
        total=fee - discount,
        paid=fee - discount,
        payment_method=payment_method,
        created_by=created_by,
    )
    InvoiceItem.objects.create(invoice=invoice, description='OPD Consultation', amount=fee)
    return invoice
