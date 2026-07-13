"""Purchase Orders — the pharmacy orders stock FROM a supplier.

Mirror of the wholesale order desk but for buying: build an order fast
(paste / quick-add / repeat / auto-suggest low stock), print & send it to the
supplier, then 'Receive' it when the goods arrive → creates a PurchaseOrder and
adds the stock (batch / expiry / cost)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required
from suppliers.models import Supplier
# reuse the same battle-tested paste parser + medicine matcher as the wholesale desk
from sales.wholesale_views import parse_line, match_medicine
from .models import (Medicine, PurchaseOrder, PurchaseItem,
                     PurchaseRequest, PurchaseRequestItem)


def last_cost(med):
    """Best guess at what this medicine costs to buy: latest batch cost, else sale price."""
    batch = med.batches.order_by('-received_at').first()
    if batch and batch.cost_price:
        return batch.cost_price
    return med.price


def _dec(v, default='0'):
    try:
        return Decimal(str(v)) if v not in (None, '') else Decimal(default)
    except (InvalidOperation, TypeError):
        return Decimal(default)


def _add_item(req, med, qty, cost=None):
    item, created = PurchaseRequestItem.objects.get_or_create(
        request=req, medicine=med,
        defaults={'quantity': qty, 'cost_price': cost if cost is not None else last_cost(med)})
    if not created:
        item.quantity += qty
        item.save(update_fields=['quantity'])
    return item


@feature_required('inventory')
def po_list(request):
    orders = PurchaseRequest.objects.select_related('supplier', 'purchase_order').all()
    return render(request, 'inventory/po_list.html', {'orders': orders})


@feature_required('inventory')
def po_create(request):
    if request.method == 'POST':
        sup_id = request.POST.get('supplier')
        supplier = Supplier.objects.filter(pk=sup_id).first() if sup_id else None
        req = PurchaseRequest.objects.create(
            supplier=supplier,
            supplier_name=request.POST.get('supplier_name', '').strip(),
            note=request.POST.get('note', '').strip(),
            created_by=request.user)
        return redirect('po_edit', pk=req.id)
    return render(request, 'inventory/po_create.html',
                  {'suppliers': Supplier.objects.all().order_by('name')})


@feature_required('inventory')
def po_edit(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    repeatable = []
    if req.supplier_id:
        repeatable = (PurchaseRequest.objects.filter(supplier=req.supplier)
                      .exclude(pk=req.pk).order_by('-created_at')[:15])
    return render(request, 'inventory/po_edit.html', {
        'req': req,
        'items': req.items.select_related('medicine'),
        'medicines': Medicine.objects.order_by('name'),
        'repeatable': repeatable,
    })


@feature_required('inventory')
def po_add_paste(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        added, unmatched = 0, []
        for raw in request.POST.get('bulk', '').splitlines():
            parsed = parse_line(raw)
            if not parsed:
                continue
            token, qty = parsed
            med = match_medicine(token)
            if med and qty >= 1:
                _add_item(req, med, qty)
                added += 1
            else:
                unmatched.append(raw.strip())
        if added:
            messages.success(request, f'Added {added} item(s) from the pasted list.')
        if unmatched:
            shown = ', '.join(unmatched[:30])
            more = f' …and {len(unmatched) - 30} more' if len(unmatched) > 30 else ''
            messages.warning(request, f"{len(unmatched)} line(s) couldn't be matched: {shown}{more}")
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_add_item(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        token = request.POST.get('medicine', '').strip()
        try:
            qty = int(request.POST.get('quantity', '1') or '1')
        except ValueError:
            qty = 1
        med = None
        if token.isdigit():
            med = Medicine.objects.filter(pk=token).first() or Medicine.objects.filter(barcode=token).first()
        if med is None:
            med = match_medicine(token.split(' — ')[0])
        if med and qty >= 1:
            _add_item(req, med, qty)
            messages.success(request, f'Added {med.name}.')
        else:
            messages.error(request, f"Couldn't find a medicine for “{token}”.")
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_autofill(request, pk):
    """Fill the order with every active medicine below its reorder level — suggested
    quantity brings it back up to the reorder level."""
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        n = 0
        for med in Medicine.objects.filter(quantity__lt=F('reorder_level')):
            need = med.reorder_level - med.quantity
            if need < 1:
                continue
            _add_item(req, med, need)
            n += 1
        if n:
            messages.success(request, f'Added {n} low-stock item(s) to reorder.')
        else:
            messages.info(request, 'Nothing is below its reorder level right now. 🎉')
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_update(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        for item in req.items.all():
            q = request.POST.get(f'qty_{item.id}')
            c = request.POST.get(f'cost_{item.id}')
            changed = False
            if q is not None:
                try:
                    qv = int(q)
                    if qv >= 1 and qv != item.quantity:
                        item.quantity = qv; changed = True
                except ValueError:
                    pass
            if c is not None:
                cv = _dec(c)
                if cv >= 0 and cv != item.cost_price:
                    item.cost_price = cv; changed = True
            if changed:
                item.save(update_fields=['quantity', 'cost_price'])
        messages.success(request, 'Order updated.')
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_item_delete(request, pk, item_id):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if req.status == PurchaseRequest.DRAFT:
        PurchaseRequestItem.objects.filter(pk=item_id, request=req).delete()
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_repeat(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        src = PurchaseRequest.objects.filter(pk=request.POST.get('source')).first()
        if src:
            n = 0
            for it in src.items.all():
                _add_item(req, it.medicine, it.quantity)
                n += 1
            messages.success(request, f'Loaded {n} item(s) from order #{src.id}.')
    return redirect('po_edit', pk=req.id)


@feature_required('inventory')
def po_print(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    return render(request, 'inventory/po_print.html',
                  {'req': req, 'items': req.items.select_related('medicine')})


@feature_required('inventory')
def po_receive(request, pk):
    """Goods arrived → confirm quantities/cost/expiry per line, create a PurchaseOrder
    and add the stock. This is the only step that changes inventory."""
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if req.status != PurchaseRequest.DRAFT:
        messages.error(request, 'This order has already been received.')
        return redirect('po_edit', pk=req.id)
    items = list(req.items.select_related('medicine'))
    if not items:
        messages.error(request, 'Add at least one item first.')
        return redirect('po_edit', pk=req.id)

    if request.method == 'POST':
        with transaction.atomic():
            supplier = req.supplier
            po = PurchaseOrder.objects.create(
                supplier=supplier,
                invoice_number=request.POST.get('invoice_number', '').strip(),
                created_by=request.user)
            total = Decimal('0.00')
            for it in items:
                try:
                    qty = int(request.POST.get(f'recv_qty_{it.id}', it.quantity) or 0)
                except ValueError:
                    qty = it.quantity
                if qty < 1:
                    continue
                cost = _dec(request.POST.get(f'recv_cost_{it.id}'), str(it.cost_price))
                expiry = request.POST.get(f'recv_exp_{it.id}') or it.medicine.expiry_date
                batch = request.POST.get(f'recv_batch_{it.id}', '').strip()
                PurchaseItem.objects.create(order=po, medicine=it.medicine, batch_number=batch,
                                            quantity=qty, cost_price=cost, expiry_date=expiry)
                it.medicine.add_stock(qty, batch_number=batch, expiry_date=expiry,
                                      cost_price=cost, supplier=supplier)
                total += cost * qty

            paid = _dec(request.POST.get('paid'), str(total))
            po.total = total
            po.paid = paid
            po.save(update_fields=['total', 'paid'])
            if supplier is not None:
                credit = total - paid
                if credit:
                    supplier.balance += credit
                    supplier.save(update_fields=['balance'])
            req.status = PurchaseRequest.RECEIVED
            req.purchase_order = po
            req.save(update_fields=['status', 'purchase_order'])
        messages.success(request, f'Received into stock as Purchase #{po.id} (total {total}, paid {paid}).')
        return redirect('purchase_detail', pk=po.id)

    return render(request, 'inventory/po_receive.html',
                  {'req': req, 'items': items, 'today': timezone.localdate()})


@feature_required('inventory')
def po_cancel(request, pk):
    req = get_object_or_404(PurchaseRequest, pk=pk)
    if request.method == 'POST' and req.status == PurchaseRequest.DRAFT:
        req.status = PurchaseRequest.CANCELLED
        req.save(update_fields=['status'])
        messages.success(request, f'Order #{req.id} cancelled.')
    return redirect('po_list')
