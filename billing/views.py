from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required, feature_required
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
    return render(request, 'billing/invoice_list.html',
                  {'invoices': invoices, 'status': status})


@feature_required('billing')
def invoice_create(request, appointment_id=None):
    appointment = None
    if appointment_id:
        appointment = get_object_or_404(Appointment, pk=appointment_id)

    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        if form.is_valid():
            invoice = create_opd_invoice(form.cleaned_data['appointment'], request.user, payment_method=form.cleaned_data['payment_method'], discount=form.cleaned_data['discount'])
            messages.success(request, 'Invoice created successfully.')
            return redirect('invoice_list')
    else:
        initial = {}
        if appointment:
            initial['appointment'] = appointment
            initial['patient'] = appointment.patient
        form = InvoiceForm(initial=initial)

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
    return render(request, 'billing/patient_billing_list.html',
                  {'rows': rows, 'q': q, 'total_due': total_due})


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
            messages.success(request, f'Payment of Rs {amount} recorded for {patient.full_name}.')
        else:
            messages.error(request, 'Enter an amount greater than zero.')
        return redirect('patient_bill', pk=patient.pk)

    summary = patient_billing_summary(patient)
    return render(request, 'billing/patient_bill.html', {'patient': patient, 's': summary})


@feature_required('billing')
def patient_bill_print(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    summary = patient_billing_summary(patient)
    return render(request, 'billing/patient_bill_print.html', {'patient': patient, 's': summary})


@feature_required('billing')
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.all_objects.select_related('patient', 'appointment', 'created_by').prefetch_related('items'),
        pk=pk)
    return render(request, 'billing/invoice_detail.html', {'invoice': invoice})


@feature_required('billing')
def invoice_mark_paid(request, pk):
    """Collect the outstanding balance — mark the invoice fully paid."""
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == 'POST':
        method = request.POST.get('payment_method', invoice.payment_method or 'CASH')
        invoice.paid = invoice.total
        invoice.payment_method = method
        invoice.save(update_fields=['paid', 'payment_method'])
        messages.success(request, f'Invoice #{invoice.id} marked paid (Rs {invoice.total}).')
    return redirect('invoice_detail', pk=invoice.pk)


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
            message=(f"🧾 Invoice #{invoice.pk} (Rs {invoice.total}) voided by "
                     f"{request.user.email} — {invoice.patient.full_name}."),
            link=f'/billing/invoices/{invoice.pk}/')
        messages.success(request, f'Invoice #{invoice.pk} has been marked VOID.')
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
    return render(request, 'billing/expense_list.html', {
        'expenses': expenses, 'total': total, 'by_cat': by_cat,
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
            messages.success(request, f'Expense recorded (Rs {exp.amount}).')
            return redirect('expense_list')
    else:
        form = ExpenseForm()
    return render(request, 'billing/expense_form.html', {'form': form, 'title': 'Record Expense'})


# ------------------------------------------------------------- cash closing
@feature_required('cashclosing')
def cash_closing_list(request):
    closings = CashClosing.objects.select_related('closed_by').all()
    return render(request, 'billing/cash_closing_list.html', {'closings': closings})


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
            messages.success(request, f'Cash closed for {cc.date}. Difference: Rs {cc.difference}.')
            return redirect('cash_closing_list')
    else:
        form = CashClosingForm(initial={'date': day, 'opening': default_opening})

    return render(request, 'billing/cash_closing_form.html', {
        'form': form, 'pos': pos, 'day': day, 'existing': existing,
        'default_opening': default_opening, 'expected_preview': expected_preview,
    })
