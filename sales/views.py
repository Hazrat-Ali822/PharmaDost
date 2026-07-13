from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required, feature_required
from customers.models import Customer
from inventory.models import Medicine
from patients.models import Patient
from .models import Sale
from .services import create_sale, return_sale

SALE_ROLES = ["ADMIN", "PHARMACIST", "WHOLESALE"]


@feature_required('pos')
@transaction.atomic
def sale_create(request):
    meds = Medicine.objects.all().order_by('name', 'brand')
    customers = Customer.objects.filter(is_active=True).order_by('name')
    patients = Patient.objects.order_by('full_name')
    if request.method == 'POST':
        sale_type = request.POST.get('sale_type', Sale.RETAIL)
        customer_id = request.POST.get('customer_id') or None
        patient_id = request.POST.get('patient_id') or None
        customer_name = request.POST.get('customer_name', '').strip()
        payment_method = request.POST.get('payment_method', 'CASH')
        order_discount = request.POST.get('discount') or 0
        paid = request.POST.get('paid')

        med_ids = request.POST.getlist('medicine_id[]')
        qtys = request.POST.getlist('quantity[]')
        prices = request.POST.getlist('unit_price[]')
        line_discounts = request.POST.getlist('line_discount[]')

        items = []
        for i, m_id in enumerate(med_ids):
            if not m_id:
                continue
            q = int(qtys[i] or 0)
            if q < 1:
                continue
            item = {"medicine_id": int(m_id), "quantity": q}
            price = prices[i].strip() if i < len(prices) and prices[i] else None
            if price:
                item["unit_price"] = price
            ld = line_discounts[i].strip() if i < len(line_discounts) and line_discounts[i] else None
            if ld:
                item["discount"] = ld
            items.append(item)

        ctx = {'meds': meds, 'customers': customers, 'patients': patients}
        if not items:
            messages.error(request, 'Please add at least one line item.')
            return render(request, 'sales/sale_create.html', ctx)

        customer = None
        if customer_id:
            customer = get_object_or_404(Customer, pk=customer_id)
        patient = None
        if patient_id:
            patient = get_object_or_404(Patient, pk=patient_id)

        try:
            sale = create_sale(
                items=items,
                sale_type=sale_type,
                customer=customer,
                patient=patient,
                customer_name=customer_name,
                discount=order_discount,
                paid=paid,
                payment_method=payment_method,
                cashier=request.user,
            )
            messages.success(request, f'Sale #{sale.id} created!')
            return redirect('sale_detail', pk=sale.id)
        except Exception as e:
            messages.error(request, str(e))
            return render(request, 'sales/sale_create.html', ctx)

    return render(request, 'sales/sale_create.html', {'meds': meds, 'customers': customers, 'patients': patients})


@feature_required('pos')
def sale_list(request):
    sales = (
        Sale.objects
        .select_related('customer', 'cashier')
        .prefetch_related('items', 'items__medicine')
        .order_by('-created_at')[:200]
    )
    return render(request, 'sales/sale_list.html', {'sales': sales})


@feature_required('pos')
def sale_detail(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related('customer', 'cashier').prefetch_related('items', 'items__medicine', 'items__batch'),
        pk=pk
    )
    return render(request, 'sales/sale_detail.html', {'sale': sale})


@feature_required('pos')
def sale_return(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        try:
            return_sale(sale, by_user=request.user)
            messages.success(request, f'Sale #{sale.id} returned. Stock restored.')
        except ValueError as e:
            messages.error(request, str(e))
        return redirect('sale_detail', pk=sale.id)
    return redirect('sale_detail', pk=sale.id)
