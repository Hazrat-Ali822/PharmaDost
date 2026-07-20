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
    from prescriptions.models import Prescription
    import json
    
    meds = Medicine.objects.all().order_by('name', 'brand')
    customers = Customer.objects.filter(is_active=True).order_by('name')
    patients = Patient.objects.order_by('full_name')
    
    prescription_id = request.GET.get('prescription_id') or request.POST.get('prescription_id')
    prescription = None
    rx_items_json = None
    preselected_patient_id = None
    
    if prescription_id:
        # scope the prefill lookup so a pharmacist can't load another hospital's
        # prescription into POS by editing the ?prescription_id= URL
        rx_lookup = Prescription.objects.all()
        if not request.user.is_superuser:
            rx_lookup = rx_lookup.filter(appointment__patient__hospital=request.user.hospital)
        prescription = get_object_or_404(rx_lookup, pk=prescription_id)
        preselected_patient_id = prescription.appointment.patient.id
        
        if request.method == 'GET':
            rx_items = []
            for item in prescription.items.all():
                estimated_qty = item.duration_days or 1
                if item.dosage:
                    parts = item.dosage.replace('-', '+').split('+')
                    try:
                        daily = sum(float(p) for p in parts if p.strip())
                        estimated_qty = int(daily * (item.duration_days or 1))
                    except ValueError:
                        pass
                rx_items.append({
                    'medicine_id': item.medicine_id or '',
                    'custom_medicine_name': item.custom_medicine_name or '',
                    'dosage': item.dosage or '',
                    'instructions': item.instructions or '',
                    'quantity': estimated_qty,
                })
            rx_items_json = json.dumps(rx_items)

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

        pending_rx = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL']).select_related('appointment__patient', 'appointment__doctor__user').prefetch_related('items__medicine')
        if not request.user.is_superuser:
            pending_rx = pending_rx.filter(appointment__patient__hospital=request.user.hospital)
        pending_prescriptions = pending_rx.order_by('-created_at')[:15]

        ctx = {
            'meds': meds, 'customers': customers, 'patients': patients,
            'prescription_id': prescription_id, 'rx_items_json': rx_items_json,
            'preselected_patient_id': preselected_patient_id,
            'pending_prescriptions': pending_prescriptions
        }
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
            if prescription:
                # Mark DISPENSED only if every prescribed (catalogued) medicine was
                # actually sold; otherwise it's a partial fill and stays in the queue.
                rx_med_ids = {i.medicine_id for i in prescription.items.all() if i.medicine_id}
                sold_med_ids = {si.medicine_id for si in sale.items.all()}
                if rx_med_ids and rx_med_ids.issubset(sold_med_ids):
                    prescription.status = 'DISPENSED'
                else:
                    prescription.status = 'PARTIAL'
                prescription.save(update_fields=['status'])
            messages.success(request, f'Sale #{sale.id} created!')
            return redirect('sale_detail', pk=sale.id)
        except Exception as e:
            messages.error(request, str(e))
            return render(request, 'sales/sale_create.html', ctx)

    pending_rx = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL']).select_related('appointment__patient', 'appointment__doctor__user').prefetch_related('items__medicine')
    if not request.user.is_superuser:
        pending_rx = pending_rx.filter(appointment__patient__hospital=request.user.hospital)
    pending_prescriptions = pending_rx.order_by('-created_at')[:15]

    return render(request, 'sales/sale_create.html', {
        'meds': meds, 'customers': customers, 'patients': patients,
        'prescription_id': prescription_id, 'rx_items_json': rx_items_json,
        'preselected_patient_id': preselected_patient_id,
        'pending_prescriptions': pending_prescriptions
    })


@feature_required('pos')
def sale_list(request):
    from prescriptions.models import Prescription
    sales = (
        Sale.objects
        .select_related('customer', 'cashier')
        .prefetch_related('items', 'items__medicine')
        .order_by('-created_at')[:200]
    )
    pending_rx = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL']).select_related('appointment__patient', 'appointment__doctor__user').prefetch_related('items__medicine')
    if not request.user.is_superuser:
        pending_rx = pending_rx.filter(appointment__patient__hospital=request.user.hospital)
    pending_prescriptions = pending_rx.order_by('-created_at')[:15]
    
    return render(request, 'sales/sale_list.html', {
        'sales': sales,
        'pending_prescriptions': pending_prescriptions,
    })


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
            result = return_sale(sale, by_user=request.user)
            q = getattr(result, 'quarantined_qty', 0)
            msg = f'Sale #{sale.id} returned. Stock restored.'
            if q:
                msg += f' Note: {q} unit(s) came back expired and are quarantined (not resellable).'
            messages.success(request, msg)
        except ValueError as e:
            messages.error(request, str(e))
        return redirect('sale_detail', pk=sale.id)
    return redirect('sale_detail', pk=sale.id)
