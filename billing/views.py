from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render

from pharma_mgmt.pagination import paginate

from accounts.decorators import role_required, feature_required
from user_mgmt.models import current_currency
from opd.models import Appointment
from patients.models import Patient
from .forms import InvoiceForm, ExpenseForm, CashClosingForm
from .models import Invoice, Expense, CashClosing
from .services import (create_opd_invoice, cash_position,
                       patient_billing_summary, collect_patient_payment,
                       outstanding_by_patient, patient_totals)

BILLING_ROLES = ["ADMIN", "RECEPTIONIST", "ACCOUNTANT"]
EXPENSE_ROLES = ["ADMIN", "ACCOUNTANT"]


@feature_required('billing')
def invoice_list(request):
    invoices = (Invoice.all_objects
                .select_related('patient', 'appointment', 'created_by')
                .prefetch_related('items')
                .all())
    status = request.GET.get('status', '').strip()
    if status == 'unpaid':
        invoices = [i for i in invoices if not i.is_paid]
    page = paginate(request, invoices)
    return render(request, 'billing/invoice_list.html',
                  {'invoices': page, 'page_obj': page, 'status': status})


@feature_required('billing')
def invoice_create(request, appointment_id=None):
    appointment = None
    if appointment_id:
        appointment = get_object_or_404(Appointment, pk=appointment_id)

    if request.method == 'POST':
        form = InvoiceForm(request.POST, user=request.user)
        if form.is_valid():
            appt = form.cleaned_data.get('appointment')
            if not appt:
                form.add_error('appointment',
                               'Select the appointment whose consultation fee you want to bill.')
            else:
                invoice = create_opd_invoice(
                    appt, request.user,
                    payment_method=form.cleaned_data['payment_method'],
                    discount=form.cleaned_data['discount'])
                messages.success(request, f'Invoice {invoice.display_no} created successfully.')
                return redirect('invoice_list')
    else:
        initial = {}
        if appointment:
            initial['appointment'] = appointment
            initial['patient'] = appointment.patient
        form = InvoiceForm(initial=initial, user=request.user)

    return render(request, 'billing/invoice_form.html', {'form': form, 'title': 'Create Invoice'})


@feature_required('billing')
def patient_billing_list(request):
    """Billing desk: find any patient (registered or a quick walk-in) and collect their
    payment — without having to open the patient profile first. Defaults to everyone who
    currently owes money."""
    q = request.GET.get('q', '').strip()
    if q:
        matches = (Patient.objects
                   .filter(Q(full_name__icontains=q) | Q(mrn__icontains=q) | Q(phone__icontains=q))
                   .order_by('full_name')[:50])
        rows = [dict(patient=p, **patient_totals(p)) for p in matches]
    else:
        rows = outstanding_by_patient()
    total_due = sum((r['outstanding'] for r in rows), Decimal('0.00'))
    page = paginate(request, rows)
    return render(request, 'billing/patient_billing_list.html',
                  {'rows': page, 'page_obj': page, 'q': q, 'total_due': total_due})


@feature_required('billing')
def patient_bill(request, pk):
    """Consolidated patient bill — all charges + one 'collect payment' action."""
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0') or '0')
        except Exception:
            amount = Decimal('0.00')
        method = request.POST.get('payment_method', 'CASH')
        note = request.POST.get('note', '').strip()
        if amount > 0:
            collect_patient_payment(patient=patient, amount=amount,
                                    method=method, user=request.user, note=note)
            messages.success(request, f'Payment of {current_currency()} {amount} recorded for {patient.full_name}.')
        else:
            messages.error(request, 'Enter an amount greater than zero.')
        return redirect('patient_bill', pk=patient.pk)

    summary = patient_billing_summary(patient)
    return render(request, 'billing/patient_bill.html', {'patient': patient, 's': summary})


@feature_required('billing')
def patient_bill_print(request, pk):
    from user_mgmt.models import SiteSettings
    patient = get_object_or_404(Patient, pk=pk)
    summary = patient_billing_summary(patient)
    branding = SiteSettings.load()
    qr_text = ''
    if branding.show_bill_qr:
        cur = branding.currency_symbol or 'Rs'
        qr_text = (
            f"{branding.brand_name or 'Sehatyar'}\n"
            f"Bill — {patient.full_name} (MRN {patient.mrn})\n"
            f"Charged: {cur} {summary['charged']}\n"
            f"Paid: {cur} {summary['paid']}\n"
            f"Outstanding: {cur} {summary['outstanding']}\n"
            f"Printed: {date.today():%d %b %Y}"
        )
    return render(request, 'billing/patient_bill_print.html',
                  {'patient': patient, 's': summary, 'qr_text': qr_text})


@feature_required('billing')
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.all_objects.select_related('patient', 'appointment', 'created_by').prefetch_related('items'),
        pk=pk)
    return render(request, 'billing/invoice_detail.html', {'invoice': invoice})


@feature_required('billing')
def invoice_mark_paid(request, pk):
    """Collect a payment against the invoice — part of the balance, or all of it.

    This used to write `invoice.paid = invoice.total` unconditionally, so a
    patient handing over Rs 500 of a Rs 1500 bill could not be recorded at all;
    the desk had to either take the whole amount or write nothing down. The model
    always supported it — the list has been showing TOTAL / PAID / BALANCE columns
    all along — it was only the view that could not.

    A blank amount still means "the whole balance", which is the common case and
    what the button used to do.
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        method = request.POST.get('payment_method', invoice.payment_method or 'CASH')
        raw = (request.POST.get('amount') or '').strip()
        if raw:
            try:
                amount = Decimal(raw)
            except (InvalidOperation, ValueError):
                messages.error(request, 'Enter a valid amount.')
                return redirect('invoice_detail', pk=invoice.pk)
            if amount <= 0:
                messages.error(request, 'The amount must be more than zero.')
                return redirect('invoice_detail', pk=invoice.pk)
            # Never let `paid` run past the total: the excess is not a payment on
            # this bill, and the balance would go negative on every report.
            amount = min(amount, invoice.balance)
        else:
            amount = invoice.balance

        invoice.paid = invoice.paid + amount
        invoice.payment_method = method
        invoice.save(update_fields=['paid', 'payment_method'])
        cur = current_currency()
        if invoice.balance > 0:
            messages.success(
                request,
                f'{cur} {amount} received for invoice {invoice.display_no}. '
                f'{cur} {invoice.balance} still outstanding.')
        else:
            messages.success(request,
                             f'Invoice {invoice.display_no} fully paid ({cur} {invoice.total}).')
    return redirect('invoice_detail', pk=invoice.pk)


@feature_required('billing')
def invoice_print(request, pk):
    """The printable sheet for one invoice.

    Every other document in the product prints — the token slip, the pharmacy
    receipt, the lab and imaging reports, the discharge summary, the whole-patient
    bill — but a single OPD / lab / IPD invoice had no print route at all, so the
    one thing a patient asks for at the counter could not be handed over.
    """
    invoice = get_object_or_404(
        Invoice.all_objects.select_related('patient', 'appointment', 'created_by')
        .prefetch_related('items'), pk=pk)
    return render(request, 'billing/invoice_print.html', {'invoice': invoice})


@feature_required('billing')
@role_required(['ADMIN'])
def invoice_void(request, pk):
    """Mark an invoice as voided/cancelled."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        invoice.status = 'VOID'
        invoice.save(update_fields=['status'])
        # Money erased from the books. With more than one admin on the account,
        # the others have no other way of learning it happened.
        from accounts.models import Notification
        Notification.notify_admins(
            hospital=request.user.hospital,
            message=(f"🧾 Invoice {invoice.display_no} ({current_currency()} {invoice.total}) voided by "
                     f"{request.user.email} — {invoice.patient.full_name}."),
            link=f'/billing/invoices/{invoice.pk}/')
        messages.success(request, f'Invoice {invoice.display_no} has been marked VOID.')
        return redirect('invoice_detail', pk=invoice.pk)
    return render(request, 'billing/invoice_confirm_void.html', {'invoice': invoice})


# ----------------------------------------------------------------- expenses
def _expense_range(request):
    """Simple from/to date filter, defaults to the current month."""
    today = date.today()

    def _parse(v):
        try:
            return date.fromisoformat(v)
        except (TypeError, ValueError):
            return None

    start = _parse(request.GET.get('from')) or today.replace(day=1)
    end = _parse(request.GET.get('to')) or today
    if end < start:
        start, end = end, start
    return start, end


@feature_required('expenses')
def expense_list(request):
    start, end = _expense_range(request)
    expenses = Expense.objects.filter(date__range=(start, end)).select_related('recorded_by')
    total = expenses.aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t']
    by_cat = {}
    for e in expenses:
        by_cat[e.get_category_display()] = by_cat.get(e.get_category_display(), Decimal('0.00')) + e.amount
    # total and by_cat above cover the whole filtered range; the page is display only.
    page = paginate(request, expenses)
    return render(request, 'billing/expense_list.html', {
        'expenses': page, 'page_obj': page, 'total': total, 'by_cat': by_cat,
        'start': start, 'end': end,
    })


@feature_required('expenses')
def expense_create(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST)
        if form.is_valid():
            exp = form.save(commit=False)
            exp.recorded_by = request.user
            exp.save()
            messages.success(request, f'Expense recorded ({current_currency()} {exp.amount}).')
            return redirect('expense_list')
    else:
        form = ExpenseForm()
    return render(request, 'billing/expense_form.html', {'form': form, 'title': 'Record Expense'})


# ------------------------------------------------------------- cash closing
@feature_required('cashclosing')
def cash_closing_list(request):
    closings = CashClosing.objects.select_related('closed_by').all()
    page = paginate(request, closings)
    return render(request, 'billing/cash_closing_list.html', {'closings': page, 'page_obj': page})


@feature_required('cashclosing')
def cash_closing_new(request):
    today = date.today()
    day = today
    d = request.GET.get('date')
    if d:
        try:
            day = date.fromisoformat(d)
        except ValueError:
            day = today

    existing = CashClosing.objects.filter(date=day).first()
    pos = cash_position(day)

    # opening defaults to the previous closing's counted cash
    prev = CashClosing.objects.filter(date__lt=day).order_by('-date').first()
    default_opening = prev.counted if prev else Decimal('0.00')
    expected_preview = default_opening + pos['net']

    if request.method == 'POST' and not existing:
        form = CashClosingForm(request.POST)
        if form.is_valid():
            cc = form.save(commit=False)
            # recompute from the authoritative day figures (don't trust posted date drift)
            cc_pos = cash_position(cc.date)
            cc.cash_in = cc_pos['cash_in']
            cc.cash_out = cc_pos['cash_out']
            cc.expected = cc.opening + cc_pos['net']
            cc.difference = cc.counted - cc.expected
            cc.closed_by = request.user
            if CashClosing.objects.filter(date=cc.date).exists():
                messages.error(request, f'{cc.date} is already closed.')
                return redirect('cash_closing_list')
            cc.save()
            messages.success(request, f'Cash closed for {cc.date}. Difference: {current_currency()} {cc.difference}.')
            return redirect('cash_closing_list')
    else:
        form = CashClosingForm(initial={'date': day, 'opening': default_opening})

    return render(request, 'billing/cash_closing_form.html', {
        'form': form, 'pos': pos, 'day': day, 'existing': existing,
        'default_opening': default_opening, 'expected_preview': expected_preview,
    })
