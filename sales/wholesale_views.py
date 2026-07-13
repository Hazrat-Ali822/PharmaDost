"""Wholesale order desk — enter a big order fast (paste / quick-add / repeat),
print it as an order sheet, and convert it to a wholesale bill in one click."""
import re
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required
from customers.models import Customer
from inventory.models import Medicine
from .models import Sale, WholesaleOrder, WholesaleOrderItem
from .services import create_sale


# --------------------------------------------------------------- paste parsing
_SEPARATORS = ["|", "\t", ";", ","]


def parse_line(raw):
    """Turn one pasted line into (name_or_code, qty). Handles 'name | 100',
    'name, 100', 'name x100', 'name 100', tab-separated (Excel), or just 'name'."""
    s = raw.strip()
    if not s:
        return None
    for sep in _SEPARATORS:
        if sep in s:
            left, right = s.rsplit(sep, 1)
            m = re.search(r"\d+", right)
            if m and left.strip():
                return left.strip(), int(m.group())
    # explicit multiplier: "name x 100" / "name ×100" / "name * 100"
    # (space required before the x so it isn't confused with an x inside a word, e.g. "Rigix 90")
    m = re.match(r"^(.*?\S)\s+[xX×\*]\s*(\d+)\s*$", s)
    if m:
        return m.group(1).strip(), int(m.group(2))
    # trailing quantity after a space: "name 100"
    m = re.match(r"^(.*?\S)\s+(\d+)\s*$", s)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return s, 1  # no quantity found → default 1 (user can fix in the table)


def match_medicine(token):
    """Resolve a pasted token to a single Medicine, or None if unknown/ambiguous."""
    token = token.strip()
    if not token:
        return None
    # exact barcode
    hit = Medicine.objects.filter(barcode=token).first()
    if hit:
        return hit
    # exact name
    hit = Medicine.objects.filter(name__iexact=token).first()
    if hit:
        return hit
    # unique partial name
    qs = list(Medicine.objects.filter(name__icontains=token)[:2])
    if len(qs) == 1:
        return qs[0]
    # unique generic
    qs = list(Medicine.objects.filter(generic_name__icontains=token)[:2])
    if len(qs) == 1:
        return qs[0]
    return None


def _wholesale_price(med):
    return med.wholesale_price if med.wholesale_price else med.price


def _add_item(order, med, qty):
    """Add qty of med to the order, merging into an existing line if present."""
    item, created = WholesaleOrderItem.objects.get_or_create(
        order=order, medicine=med,
        defaults={"quantity": qty, "unit_price": _wholesale_price(med)})
    if not created:
        item.quantity += qty
        item.save(update_fields=["quantity"])
    return item


# ---------------------------------------------------------------------- views
@feature_required('pos')
def order_list(request):
    orders = WholesaleOrder.objects.select_related("customer", "sale").all()
    return render(request, "sales/wholesale_list.html", {"orders": orders})


@feature_required('pos')
def order_create(request):
    if request.method == "POST":
        cust_id = request.POST.get("customer")
        customer = Customer.objects.filter(pk=cust_id).first() if cust_id else None
        order = WholesaleOrder.objects.create(
            customer=customer,
            customer_name=request.POST.get("customer_name", "").strip(),
            note=request.POST.get("note", "").strip(),
            created_by=request.user)
        return redirect("wholesale_order_edit", pk=order.id)
    return render(request, "sales/wholesale_create.html",
                  {"customers": Customer.objects.filter(is_active=True).order_by("name")})


@feature_required('pos')
def order_edit(request, pk):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    # previous orders of the same customer, to "repeat"
    repeatable = []
    if order.customer_id:
        repeatable = (WholesaleOrder.objects
                      .filter(customer=order.customer).exclude(pk=order.pk)
                      .order_by("-created_at")[:15])
    return render(request, "sales/wholesale_edit.html", {
        "order": order,
        "items": order.items.select_related("medicine"),
        "medicines": Medicine.objects.order_by("name"),
        "repeatable": repeatable,
    })


@feature_required('pos')
def order_add_paste(request, pk):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method == "POST" and order.status == WholesaleOrder.DRAFT:
        text = request.POST.get("bulk", "")
        added, unmatched = 0, []
        for raw in text.splitlines():
            parsed = parse_line(raw)
            if not parsed:
                continue
            token, qty = parsed
            med = match_medicine(token)
            if med and qty >= 1:
                _add_item(order, med, qty)
                added += 1
            else:
                unmatched.append(raw.strip())
        if added:
            messages.success(request, f"Added {added} item(s) from the pasted list.")
        if unmatched:
            shown = ", ".join(unmatched[:30])
            more = f" …and {len(unmatched) - 30} more" if len(unmatched) > 30 else ""
            messages.warning(request,
                f"{len(unmatched)} line(s) couldn't be matched — add these by hand: {shown}{more}")
        if not added and not unmatched:
            messages.error(request, "Nothing to add — the box was empty.")
    return redirect("wholesale_order_edit", pk=order.id)


@feature_required('pos')
def order_add_item(request, pk):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method == "POST" and order.status == WholesaleOrder.DRAFT:
        token = request.POST.get("medicine", "").strip()
        try:
            qty = int(request.POST.get("quantity", "1") or "1")
        except ValueError:
            qty = 1
        # the datalist submits "Name — barcode"; also allow raw id, name or barcode
        med = None
        if token.isdigit():
            med = Medicine.objects.filter(pk=token).first() or Medicine.objects.filter(barcode=token).first()
        if med is None:
            med = match_medicine(token.split(" — ")[0])
        if med and qty >= 1:
            _add_item(order, med, qty)
            messages.success(request, f"Added {med.name}.")
        else:
            messages.error(request, f"Couldn't find a medicine for “{token}”.")
    return redirect("wholesale_order_edit", pk=order.id)


@feature_required('pos')
def order_item_delete(request, pk, item_id):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if order.status == WholesaleOrder.DRAFT:
        WholesaleOrderItem.objects.filter(pk=item_id, order=order).delete()
    return redirect("wholesale_order_edit", pk=order.id)


@feature_required('pos')
def order_item_update(request, pk):
    """Bulk-save quantities/prices edited in the table."""
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method == "POST" and order.status == WholesaleOrder.DRAFT:
        for item in order.items.all():
            q = request.POST.get(f"qty_{item.id}")
            p = request.POST.get(f"price_{item.id}")
            changed = False
            if q is not None:
                try:
                    qv = int(q)
                    if qv >= 1 and qv != item.quantity:
                        item.quantity = qv
                        changed = True
                except ValueError:
                    pass
            if p is not None:
                try:
                    pv = Decimal(p)
                    if pv >= 0 and pv != item.unit_price:
                        item.unit_price = pv
                        changed = True
                except Exception:
                    pass
            if changed:
                item.save(update_fields=["quantity", "unit_price"])
        messages.success(request, "Order updated.")
    return redirect("wholesale_order_edit", pk=order.id)


@feature_required('pos')
def order_repeat(request, pk):
    """Copy the items of a previous order for this customer into this one."""
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method == "POST" and order.status == WholesaleOrder.DRAFT:
        src = WholesaleOrder.objects.filter(pk=request.POST.get("source")).first()
        if src:
            n = 0
            for it in src.items.all():
                _add_item(order, it.medicine, it.quantity)
                n += 1
            messages.success(request, f"Loaded {n} item(s) from order #{src.id}.")
    return redirect("wholesale_order_edit", pk=order.id)


@feature_required('pos')
def order_print(request, pk):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    return render(request, "sales/wholesale_print.html",
                  {"order": order, "items": order.items.select_related("medicine")})


@feature_required('pos')
def order_convert(request, pk):
    """Turn the order sheet into a real wholesale Sale (deducts stock)."""
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method != "POST":
        return redirect("wholesale_order_edit", pk=order.id)
    if order.status != WholesaleOrder.DRAFT:
        messages.error(request, "This order is already billed.")
        return redirect("wholesale_order_edit", pk=order.id)
    if order.customer is None:
        messages.error(request, "A wholesale bill needs a registered customer. Edit the order and pick one.")
        return redirect("wholesale_order_edit", pk=order.id)
    items = [{"medicine_id": it.medicine_id, "quantity": it.quantity, "unit_price": it.unit_price}
             for it in order.items.all()]
    if not items:
        messages.error(request, "Add at least one item first.")
        return redirect("wholesale_order_edit", pk=order.id)
    paid = request.POST.get("paid", "")
    method = request.POST.get("payment_method", "CREDIT")
    try:
        sale = create_sale(items=items, sale_type=Sale.WHOLESALE, customer=order.customer,
                           paid=(paid if paid != "" else Decimal("0.00")),
                           payment_method=method, cashier=request.user)
    except ValueError as e:
        messages.error(request, f"Couldn't bill: {e}")
        return redirect("wholesale_order_edit", pk=order.id)
    order.status = WholesaleOrder.BILLED
    order.sale = sale
    order.save(update_fields=["status", "sale"])
    messages.success(request, f"Order billed as Sale #{sale.id} (Rs {sale.total}). Stock updated.")
    return redirect("sale_detail", pk=sale.id)


@feature_required('pos')
def order_cancel(request, pk):
    order = get_object_or_404(WholesaleOrder, pk=pk)
    if request.method == "POST" and order.status == WholesaleOrder.DRAFT:
        order.status = WholesaleOrder.CANCELLED
        order.save(update_fields=["status"])
        messages.success(request, f"Order #{order.id} cancelled.")
    return redirect("wholesale_order_list")
