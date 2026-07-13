from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Supplier
from .forms import SupplierForm, SupplierPaymentForm
from .services import record_supplier_payment
from accounts.decorators import role_required, feature_required

@feature_required('suppliers')
def supplier_list(request):
    suppliers = Supplier.objects.all()
    return render(request, 'suppliers/supplier_list.html', {'suppliers': suppliers})

@role_required(["ADMIN", "PHARMACIST"])
def supplier_create(request):
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Supplier added!')
            return redirect('supplier_list')
    else:
        form = SupplierForm()
    return render(request, 'suppliers/supplier_form.html', {'form': form, 'title': 'Add Supplier'})

@role_required(["ADMIN", "PHARMACIST"])
def supplier_edit(request, pk):
    obj = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Supplier updated!')
            return redirect('supplier_list')
    else:
        form = SupplierForm(instance=obj)
    return render(request, 'suppliers/supplier_form.html', {'form': form, 'title': 'Edit Supplier'})

@role_required(["ADMIN", "PHARMACIST"])
def supplier_delete(request, pk):
    obj = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Supplier deleted.')
        return redirect('supplier_list')
    # Reuse the generic confirm template from inventory
    return render(request, 'inventory/confirm_delete.html', {'object': obj, 'cancel_url': 'supplier_list'})


@feature_required('suppliers')
def supplier_ledger(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    # local imports to avoid any import cycle with the inventory app
    from inventory.models import PurchaseOrder, PurchaseReturn

    purchases = PurchaseOrder.objects.filter(supplier=supplier)
    payments = supplier.payments.all()
    returns = PurchaseReturn.objects.filter(supplier=supplier)

    entries = []
    for p in purchases:
        entries.append({"when": p.received_at, "kind": "PURCHASE", "ref": f"PO #{p.id} {p.invoice_number}",
                        "debit": p.total, "credit": p.paid})   # we owe total, paid now reduces it
    for pay in payments:
        entries.append({"when": pay.created_at, "kind": "PAYMENT",
                        "ref": f"{pay.get_method_display()}" + (f" - {pay.notes}" if pay.notes else ""),
                        "debit": 0, "credit": pay.amount})
    for r in returns:
        entries.append({"when": r.created_at, "kind": "RETURN", "ref": f"Return #{r.id} ({r.get_reason_display()})",
                        "debit": 0, "credit": r.total})

    entries.sort(key=lambda e: e["when"])
    running = 0
    for e in entries:
        running += float(e["debit"]) - float(e["credit"])
        e["running"] = running

    return render(request, 'suppliers/supplier_ledger.html', {'supplier': supplier, 'entries': entries})


@feature_required('suppliers')
def supplier_payment_add(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        form = SupplierPaymentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                record_supplier_payment(supplier, amount=cd['amount'], method=cd['method'],
                                        notes=cd['notes'], by_user=request.user, date=cd['date'])
                messages.success(request, 'Supplier payment recorded.')
                return redirect('supplier_ledger', pk=supplier.pk)
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = SupplierPaymentForm()
    return render(request, 'suppliers/supplier_payment_form.html',
                  {'form': form, 'supplier': supplier, 'title': f'Pay Supplier - {supplier.name}'})
