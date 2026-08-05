from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required

from .forms import PanelForm, PanelPaymentForm
from .models import Panel
from .services import outstanding_for, outstanding_map, record_payment


@feature_required('panel')
def panel_list(request):
    q = (request.GET.get('q') or '').strip()
    ptype = request.GET.get('type') or ''
    panels = Panel.objects.all()
    if q:
        panels = panels.filter(Q(name__icontains=q) | Q(contact_person__icontains=q)
                               | Q(phone__icontains=q))
    if ptype:
        panels = panels.filter(type=ptype)
    panels = list(panels)
    outstanding = outstanding_map(panels)
    for p in panels:
        p.outstanding = outstanding.get(p.pk, 0)
    total_outstanding = sum((p.outstanding for p in panels), 0)
    return render(request, 'panels/panel_list.html', {
        'panels': panels, 'q': q, 'ptype': ptype,
        'total_outstanding': total_outstanding,
        'type_choices': Panel.TYPE_CHOICES,
    })


@feature_required('panel')
def panel_create(request):
    if request.method == 'POST':
        form = PanelForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Panel added.')
            return redirect('panel_list')
    else:
        form = PanelForm()
    return render(request, 'panels/panel_form.html', {'form': form, 'title': 'Add Panel'})


@feature_required('panel')
def panel_edit(request, pk):
    panel = get_object_or_404(Panel, pk=pk)
    if request.method == 'POST':
        form = PanelForm(request.POST, instance=panel)
        if form.is_valid():
            form.save()
            messages.success(request, 'Panel updated.')
            return redirect('panel_list')
    else:
        form = PanelForm(instance=panel)
    return render(request, 'panels/panel_form.html',
                  {'form': form, 'title': f'Edit {panel.name}', 'panel': panel})


@feature_required('panel')
def panel_ledger(request, pk):
    panel = get_object_or_404(Panel, pk=pk)
    entries = []
    # Debits: each claim (invoice) billed to the panel. What the panel owes for
    # it is total − whatever the patient paid at the counter (the co-pay).
    for inv in panel.invoices.select_related('patient').all():
        entries.append({
            'when': inv.created_at,
            'kind': 'Claim',
            'ref': f'{inv.display_no} · {inv.patient.full_name}',
            'status': inv.get_claim_status_display() if inv.claim_status else '',
            'claim_status': inv.claim_status,
            'claim_number': inv.claim_number,
            'debit': inv.total or 0,
            'credit': inv.paid or 0,   # co-pay collected from the patient
            'invoice_id': inv.pk,
        })
    # Pharmacy (POS) sales billed to the panel — same debit/credit shape as a claim,
    # but not an invoice, so no editable claim status.
    for s in panel.sales.filter(is_returned=False).select_related('patient').all():
        who = s.patient.full_name if s.patient else (s.customer_name or 'Walk-in')
        entries.append({
            'when': s.created_at,
            'kind': 'Pharmacy',
            'ref': f'Sale #{s.id} · {who}',
            'status': '', 'claim_status': '', 'claim_number': '',
            'debit': s.total or 0,
            'credit': s.paid or 0,
            'invoice_id': None,
        })
    # Credits: payments received from the panel.
    for p in panel.payments.all():
        entries.append({
            'when': p.created_at,
            'kind': 'Payment',
            'ref': f'{p.get_method_display()}{" · " + p.reference if p.reference else ""}',
            'status': '',
            'debit': 0,
            'credit': p.amount,
            'invoice_id': None,
        })
    entries.sort(key=lambda e: e['when'])
    running = 0
    for e in entries:
        running += float(e['debit']) - float(e['credit'])
        e['running'] = running
    from billing.models import Invoice
    return render(request, 'panels/panel_ledger.html', {
        'panel': panel, 'entries': entries,
        'outstanding': outstanding_for(panel),
        'claim_status_choices': Invoice.CLAIM_STATUS_CHOICES,
    })


@feature_required('panel')
def payment_create(request, pk):
    panel = get_object_or_404(Panel, pk=pk)
    if request.method == 'POST':
        form = PanelPaymentForm(request.POST)
        if form.is_valid():
            try:
                record_payment(
                    panel,
                    amount=form.cleaned_data['amount'],
                    method=form.cleaned_data['method'],
                    reference=form.cleaned_data['reference'],
                    notes=form.cleaned_data['notes'],
                    received_by=request.user,
                    date=form.cleaned_data['date'],
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, 'Payment recorded.')
                return redirect('panel_ledger', pk=panel.pk)
    else:
        form = PanelPaymentForm()
    return render(request, 'panels/payment_form.html', {
        'form': form, 'panel': panel, 'title': f'Receive Payment — {panel.name}',
        'outstanding': outstanding_for(panel),
    })


@feature_required('panel')
def claim_update(request, pk):
    """Update the claim status / number on a single panel invoice from the ledger."""
    from billing.models import Invoice
    if request.method != 'POST':
        return redirect('panel_list')
    invoice = get_object_or_404(Invoice.all_objects, pk=pk)
    status = request.POST.get('claim_status', '')
    valid = {c[0] for c in Invoice.CLAIM_STATUS_CHOICES}
    if status in valid:
        invoice.claim_status = status
    invoice.claim_number = (request.POST.get('claim_number') or '').strip()
    invoice.save(update_fields=['claim_status', 'claim_number'])
    messages.success(request, 'Claim updated.')
    if invoice.panel_id:
        return redirect('panel_ledger', pk=invoice.panel_id)
    return redirect('panel_list')
