from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from .models import Medicine
from .forms import MedicineForm
from accounts.decorators import role_required, feature_required
from .models import (
    PurchaseOrder, PurchaseItem, StockBatch, StockAdjustment, PurchaseReturn,
)
from .services import apply_adjustment, create_purchase_return
from suppliers.models import Supplier
from django.db import transaction
from django.shortcuts import reverse


@feature_required('inventory')
def dashboard(request):
    low_stock = Medicine.objects.low_stock()
    expiring = Medicine.objects.expiring_soon(30)
    return render(request, 'dashboard.html', {
        'low_stock': low_stock,
        'expiring': expiring,
        'today': timezone.localdate(),
    })


@feature_required('inventory')
def medicine_list(request):
    q = request.GET.get('q', '').strip()
    meds = Medicine.objects.all()
    if q:
        meds = meds.filter(
            Q(name__icontains=q) | Q(generic_name__icontains=q) |
            Q(brand__icontains=q) | Q(barcode__icontains=q)
        )
    return render(request, 'inventory/medicine_list.html', {'meds': meds, 'q': q})


@feature_required('inventory')
def medicine_create(request):
    if request.method == 'POST':
        form = MedicineForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Medicine added successfully!')
            return redirect('medicine_list')
    else:
        form = MedicineForm()
    return render(request, 'inventory/medicine_form.html', {'form': form, 'title': 'Add Medicine'})


@feature_required('inventory')
def medicine_edit(request, pk):
    med = get_object_or_404(Medicine, pk=pk)
    if request.method == 'POST':
        form = MedicineForm(request.POST, instance=med)
        if form.is_valid():
            form.save()
            messages.success(request, 'Medicine updated successfully!')
            return redirect('medicine_list')
    else:
        form = MedicineForm(instance=med)
    return render(request, 'inventory/medicine_form.html', {'form': form, 'title': 'Edit Medicine'})


@feature_required('inventory')
def medicine_delete(request, pk):
    med = get_object_or_404(Medicine, pk=pk)
    if request.method == 'POST':
        med.soft_delete()
        messages.success(request, 'Medicine archived successfully.')
        return redirect('medicine_list')
    return render(request, 'inventory/confirm_delete.html', {'object': med, 'cancel_url': 'medicine_list'})


@feature_required('inventory')
@transaction.atomic
def purchase_create(request):
    meds = Medicine.all_objects.filter(is_active=True).order_by('name', 'brand')
    suppliers = Supplier.objects.all()
    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        invoice = request.POST.get('invoice_number', '').strip()
        med_ids = request.POST.getlist('medicine_id[]')
        qtys = request.POST.getlist('quantity[]')
        costs = request.POST.getlist('cost_price[]')
        exps = request.POST.getlist('expiry_date[]')

        if not med_ids:
            messages.error(request, 'Add at least one purchase line')
            return render(request, 'inventory/purchase_form.html', {'meds': meds, 'suppliers': suppliers})

        supplier = None
        if supplier_id:
            supplier = Supplier.objects.select_for_update().filter(pk=supplier_id).first()

        po = PurchaseOrder.objects.create(supplier=supplier, invoice_number=invoice, created_by=request.user)
        from decimal import Decimal as _D
        total = _D('0.00')
        for i, m_id in enumerate(med_ids):
            if not m_id:
                continue
            med = Medicine.all_objects.get(pk=int(m_id))
            qty = int(qtys[i] or 0)
            if qty < 1:
                continue
            cost = _D(str(costs[i])) if costs[i] else _D(str(med.price))
            expiry = exps[i] or med.expiry_date
            PurchaseItem.objects.create(order=po, medicine=med, batch_number='', quantity=qty, cost_price=cost, expiry_date=expiry)
            med.add_stock(qty, batch_number='', expiry_date=expiry, cost_price=cost, supplier=supplier)
            total += cost * qty

        paid_raw = request.POST.get('paid', '')
        paid = total if paid_raw.strip() == '' else _D(str(paid_raw))
        po.total = total
        po.paid = paid
        po.save(update_fields=['total', 'paid'])

        # increase supplier payable by the unpaid portion
        if supplier is not None:
            credit = total - paid
            if credit:
                supplier.balance += credit
                supplier.save(update_fields=['balance'])

        messages.success(request, f'Purchase Order #{po.id} recorded (total {total}, paid {paid})')
        return redirect('purchase_detail', pk=po.id)

    return render(request, 'inventory/purchase_form.html', {'meds': meds, 'suppliers': suppliers})


@feature_required('inventory')
def purchase_list(request):
    orders = PurchaseOrder.objects.prefetch_related('items', 'items__medicine').order_by('-received_at')[:200]
    return render(request, 'inventory/purchase_list.html', {'orders': orders})


@feature_required('inventory')
def purchase_detail(request, pk):
    order = get_object_or_404(PurchaseOrder.objects.prefetch_related('items', 'items__medicine'), pk=pk)
    return render(request, 'inventory/purchase_detail.html', {'order': order})


# ---------------------------------------------------------------------------
# Stock adjustments
# ---------------------------------------------------------------------------

def _batch_choices(only_in_stock=False):
    qs = StockBatch.objects.select_related('medicine').order_by('medicine__name', 'expiry_date')
    if only_in_stock:
        qs = qs.filter(quantity__gt=0)
    return qs


@feature_required('inventory')
def adjustment_list(request):
    adjustments = (StockAdjustment.objects
                   .select_related('batch', 'batch__medicine', 'by_user')
                   .order_by('-created_at')[:200])
    return render(request, 'inventory/adjustment_list.html', {'adjustments': adjustments})


@feature_required('inventory')
def adjustment_create(request):
    batches = _batch_choices()
    if request.method == 'POST':
        try:
            batch = StockBatch.objects.get(pk=request.POST.get('batch'))
            qty_change = int(request.POST.get('qty_change') or 0)
            apply_adjustment(
                batch=batch,
                qty_change=qty_change,
                reason=request.POST.get('reason', 'OTHER'),
                notes=request.POST.get('notes', '').strip(),
                by_user=request.user,
            )
            messages.success(request, 'Stock adjustment recorded.')
            return redirect('adjustment_list')
        except (StockBatch.DoesNotExist, ValueError) as e:
            messages.error(request, str(e) or 'Invalid batch.')
    return render(request, 'inventory/adjustment_form.html', {
        'batches': batches,
        'reasons': StockAdjustment.REASON_CHOICES,
    })


# ---------------------------------------------------------------------------
# Purchase returns (to supplier)
# ---------------------------------------------------------------------------

@feature_required('inventory')
def preturn_list(request):
    returns = (PurchaseReturn.objects
               .select_related('supplier', 'created_by')
               .prefetch_related('items')
               .order_by('-created_at')[:200])
    return render(request, 'inventory/preturn_list.html', {'returns': returns})


@feature_required('inventory')
def preturn_create(request):
    batches = _batch_choices(only_in_stock=True)
    suppliers = Supplier.objects.all()
    if request.method == 'POST':
        batch_ids = request.POST.getlist('batch_id[]')
        qtys = request.POST.getlist('quantity[]')
        costs = request.POST.getlist('cost_price[]')
        items = []
        for i, b_id in enumerate(batch_ids):
            if not b_id:
                continue
            q = int(qtys[i] or 0)
            if q < 1:
                continue
            item = {"batch_id": int(b_id), "quantity": q}
            c = costs[i].strip() if i < len(costs) and costs[i] else None
            if c:
                item["cost_price"] = c
            items.append(item)

        if not items:
            messages.error(request, 'Add at least one item to return.')
        else:
            supplier = None
            sid = request.POST.get('supplier')
            if sid:
                supplier = Supplier.objects.filter(pk=sid).first()
            try:
                ret = create_purchase_return(
                    supplier=supplier,
                    reason=request.POST.get('reason', 'EXPIRY'),
                    notes=request.POST.get('notes', '').strip(),
                    items=items,
                    by_user=request.user,
                )
                messages.success(request, f'Purchase return #{ret.id} recorded.')
                return redirect('preturn_detail', pk=ret.id)
            except (StockBatch.DoesNotExist, ValueError) as e:
                messages.error(request, str(e))

    return render(request, 'inventory/preturn_form.html', {
        'batches': batches,
        'suppliers': suppliers,
        'reasons': PurchaseReturn.REASON_CHOICES,
    })


@feature_required('inventory')
def preturn_detail(request, pk):
    ret = get_object_or_404(
        PurchaseReturn.objects.select_related('supplier').prefetch_related('items', 'items__batch', 'items__batch__medicine'),
        pk=pk,
    )
    return render(request, 'inventory/preturn_detail.html', {'ret': ret})