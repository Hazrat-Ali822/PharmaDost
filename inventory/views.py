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
    import json
    import datetime
    from datetime import timedelta
    from decimal import Decimal
    from django.db.models import Sum, Q
    from billing.models import Invoice, InvoiceItem, Expense
    from sales.models import Sale

    low_stock = Medicine.objects.low_stock()
    expiring = Medicine.objects.expiring_soon(30)
    
    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    try:
        if start_date_str:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        else:
            start_date = today - datetime.timedelta(days=30)
    except ValueError:
        start_date = today - datetime.timedelta(days=30)

    try:
        if end_date_str:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            end_date = today
    except ValueError:
        end_date = today

    # 7-day line chart (using last 7 days from today)
    seven_days_ago = today - timedelta(days=6)
    labels = []
    daily_income = []
    daily_expense = []
    
    for i in range(7):
        day = seven_days_ago + timedelta(days=i)
        labels.append(day.strftime("%a %d"))
        day_inv = Invoice.objects.filter(created_at__date=day).aggregate(s=Sum('paid'))['s'] or 0
        day_sale = Sale.objects.filter(created_at__date=day, is_returned=False).aggregate(s=Sum('paid'))['s'] or 0
        daily_income.append(float(day_inv + day_sale))
        day_exp = Expense.objects.filter(date=day).aggregate(s=Sum('amount'))['s'] or 0
        daily_expense.append(float(day_exp))
        
    # Departmental Revenue & Activity Calculations for Selected Date Range
    invs = Invoice.objects.filter(created_at__date__range=[start_date, end_date])
    if request.user.hospital:
        invs = invs.filter(patient__hospital=request.user.hospital)

    items = InvoiceItem.objects.filter(invoice__in=invs).select_related('invoice')
    
    opd_rev = Decimal('0.00')
    lab_rev = Decimal('0.00')
    imaging_rev = Decimal('0.00')
    other_rev = Decimal('0.00')
    
    for item in items:
        desc = item.description
        amt = item.amount
        factor = Decimal('1.00')
        if item.invoice.total > 0:
            factor = Decimal(str(item.invoice.paid)) / Decimal(str(item.invoice.total))
        scaled_amt = amt * factor
        
        if desc == 'OPD Consultation':
            if item.invoice.appointment and item.invoice.appointment.status == 'DONE':
                opd_rev += scaled_amt
        elif desc.startswith('Lab:'):
            lab_rev += scaled_amt
        elif any(desc.startswith(prefix) for prefix in ['Ultrasound:', 'X-Ray:', 'CT Scan:', 'MRI:', 'ECG:', 'Echocardiography:', 'Mammography:', 'ULTRASOUND:', 'XRAY:', 'CT:', 'MRI:', 'ECG:', 'ECHO:', 'MAMMO:']):
            imaging_rev += scaled_amt
        else:
            other_rev += scaled_amt

    pharmacy_sales = Sale.objects.filter(created_at__date__range=[start_date, end_date], is_returned=False)
    if request.user.hospital:
        pharmacy_sales = pharmacy_sales.filter(patient__hospital=request.user.hospital)
    pharmacy_rev = pharmacy_sales.aggregate(s=Sum('paid'))['s'] or Decimal('0.00')

    # Total Income and Expense for the selected date range
    total_income_range = float(opd_rev + lab_rev + imaging_rev + other_rev + pharmacy_rev)
    total_expense_range = float(Expense.objects.filter(date__range=[start_date, end_date]).aggregate(s=Sum('amount'))['s'] or 0)

    # Calculate 30-day summaries for comparison tiles
    thirty_days_ago = today - timedelta(days=29)
    tot_inv_30d = Invoice.objects.filter(created_at__date__gte=thirty_days_ago).aggregate(s=Sum('paid'))['s'] or 0
    tot_sale_30d = Sale.objects.filter(created_at__date__gte=thirty_days_ago, is_returned=False).aggregate(s=Sum('paid'))['s'] or 0
    total_income_30d = float(tot_inv_30d + tot_sale_30d)
    total_expense_30d = float(Expense.objects.filter(date__gte=thirty_days_ago).aggregate(s=Sum('amount'))['s'] or 0)
    
    return render(request, 'dashboard.html', {
        'low_stock': low_stock,
        'expiring': expiring,
        'today': today,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'finance_labels': json.dumps(labels),
        'finance_income': json.dumps(daily_income),
        'finance_expense': json.dumps(daily_expense),
        'total_income_30d': total_income_30d,
        'total_expense_30d': total_expense_30d,
        # Departmental Breakdown
        'opd_rev': opd_rev,
        'lab_rev': lab_rev,
        'imaging_rev': imaging_rev,
        'pharmacy_rev': pharmacy_rev,
        'other_rev': other_rev,
        'total_income_range': total_income_range,
        'total_expense_range': total_expense_range,
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


@feature_required('inventory')
def medicine_import_catalog(request):
    import datetime
    from decimal import Decimal
    from django.db import IntegrityError
    from django.views.decorators.http import require_POST
    
    if request.method != 'POST':
        return redirect('medicine_list')

    # Essential medicine catalog based on 20+ years of pharmacist expertise (A-Z Pakistani brands)
    STANDARD_MEDICINES = [
        # A
        {"name": "Amoxil 500mg", "generic_name": "Amoxicillin", "brand": "GSK", "category": "CAPSULE", "manufacturer": "GSK Pharma"},
        {"name": "Augmentin 625mg", "generic_name": "Co-amoxiclav", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Augmentin 1g", "generic_name": "Co-amoxiclav", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Augmentin DS Susp", "generic_name": "Co-amoxiclav", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Azomax 500mg", "generic_name": "Azithromycin", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Arinac", "generic_name": "Ibuprofen + Pseudoephedrine", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Arinac Forte", "generic_name": "Ibuprofen + Pseudoephedrine", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Avil", "generic_name": "Pheniramine Maleate", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Angised 0.5mg", "generic_name": "Glyceryl Trinitrate", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Ascard 75mg", "generic_name": "Aspirin", "brand": "Atco", "category": "TABLET", "manufacturer": "Atco Laboratories"},
        {"name": "Actifed", "generic_name": "Triprolidine + Pseudoephedrine", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Aerius 5mg", "generic_name": "Desloratadine", "brand": "Organon", "category": "TABLET", "manufacturer": "Organon"},
        {"name": "Atarax 25mg", "generic_name": "Hydroxyzine HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Adalat 20mg Retard", "generic_name": "Nifedipine", "brand": "Bayer", "category": "TABLET", "manufacturer": "Bayer Pakistan"},

        # B
        {"name": "Brufen 400mg", "generic_name": "Ibuprofen", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Brufen 600mg", "generic_name": "Ibuprofen", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Brufen Syrup", "generic_name": "Ibuprofen", "brand": "Abbott", "category": "SYRUP", "manufacturer": "Abbott Laboratories"},
        {"name": "Betnovate Cream", "generic_name": "Betamethasone", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Betnovate-N Cream", "generic_name": "Betamethasone Valerate + Neomycin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Betnovate-C Cream", "generic_name": "Betamethasone Valerate + Clioquinol", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Buscopan 10mg", "generic_name": "Hyoscine Butylbromide", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Buscopan Injection", "generic_name": "Hyoscine Butylbromide", "brand": "Sanofi", "category": "INJECTION", "manufacturer": "Sanofi-Aventis"},
        {"name": "Biseptol", "generic_name": "Co-trimoxazole", "brand": "Polfa", "category": "TABLET", "manufacturer": "Polfa"},
        {"name": "Bonjela Gel", "generic_name": "Choline Salicylate", "brand": "Reckitt", "category": "CREAM", "manufacturer": "Reckitt Benckiser"},

        # C
        {"name": "Calpol Suspension", "generic_name": "Paracetamol", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Cac 1000 Plus", "generic_name": "Calcium + Vitamin C", "brand": "Sandoz", "category": "SACHET", "manufacturer": "Sandoz Pakistan"},
        {"name": "Capoten 25mg", "generic_name": "Captopril", "brand": "Bristol-Myers", "category": "TABLET", "manufacturer": "Bristol-Myers Squibb"},
        {"name": "Concor 2.5mg", "generic_name": "Bisoprolol Fumarate", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Concor 5mg", "generic_name": "Bisoprolol Fumarate", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Ciproxin 500mg", "generic_name": "Ciprofloxacin", "brand": "Bayer", "category": "TABLET", "manufacturer": "Bayer Pakistan"},
        {"name": "Ceclor 250mg", "generic_name": "Cefaclor", "brand": "Eli Lilly", "category": "CAPSULE", "manufacturer": "Eli Lilly"},
        {"name": "Colofac 135mg", "generic_name": "Mebeverine HCl", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Cranmax Sachet", "generic_name": "Cranberry Extract", "brand": "Herbion", "category": "SACHET", "manufacturer": "Herbion Pakistan"},
        {"name": "Calpol 500mg", "generic_name": "Paracetamol", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Cefspan 400mg", "generic_name": "Cefixime", "brand": "Barrett Hodgson", "category": "CAPSULE", "manufacturer": "Barrett Hodgson"},
        {"name": "Co-Diovan", "generic_name": "Valsartan + Hydrochlorothiazide", "brand": "Novartis", "category": "TABLET", "manufacturer": "Novartis Pakistan"},
        {"name": "Combivair Inhaler", "generic_name": "Formoterol + Budesonide", "brand": "Getz", "category": "INHALER", "manufacturer": "Getz Pharma"},
        {"name": "Cremaffin Syrup", "generic_name": "Liquid Paraffin + Milk of Magnesia", "brand": "Abbott", "category": "SYRUP", "manufacturer": "Abbott Laboratories"},

        # D
        {"name": "Disprin 300mg", "generic_name": "Aspirin", "brand": "Reckitt", "category": "TABLET", "manufacturer": "Reckitt Benckiser"},
        {"name": "Decadron 4mg", "generic_name": "Dexamethasone", "brand": "Organon", "category": "TABLET", "manufacturer": "Organon"},
        {"name": "Daktarin Cream", "generic_name": "Miconazole Nitrate", "brand": "Janssen", "category": "CREAM", "manufacturer": "Janssen Pharma"},
        {"name": "Daktarin Oral Gel", "generic_name": "Miconazole Nitrate", "brand": "Janssen", "category": "CREAM", "manufacturer": "Janssen Pharma"},
        {"name": "Duphaston 10mg", "generic_name": "Dydrogesterone", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Daonil 5mg", "generic_name": "Glibenclamide", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Duspatalin 135mg", "generic_name": "Mebeverine HCl", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Diovan 80mg", "generic_name": "Valsartan", "brand": "Novartis", "category": "TABLET", "manufacturer": "Novartis Pakistan"},
        {"name": "Detrol 2mg", "generic_name": "Tolterodine Tartrate", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Dicloran 50mg", "generic_name": "Diclofenac Sodium", "brand": "Sami", "category": "TABLET", "manufacturer": "Sami Pharmaceuticals"},
        {"name": "Dicloran Injection", "generic_name": "Diclofenac Sodium", "brand": "Sami", "category": "INJECTION", "manufacturer": "Sami Pharmaceuticals"},

        # E
        {"name": "Evion 400mg", "generic_name": "Vitamin E", "brand": "Merck", "category": "CAPSULE", "manufacturer": "Merck Pakistan"},
        {"name": "Evion 600mg", "generic_name": "Vitamin E", "brand": "Merck", "category": "CAPSULE", "manufacturer": "Merck Pakistan"},
        {"name": "Entamizole", "generic_name": "Diloxanide Furoate + Metronidazole", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Entamizole DS", "generic_name": "Diloxanide Furoate + Metronidazole", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Entamizole Suspension", "generic_name": "Diloxanide Furoate + Metronidazole", "brand": "Abbott", "category": "SYRUP", "manufacturer": "Abbott Laboratories"},
        {"name": "Esoral 20mg", "generic_name": "Esomeprazole", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Erythrocin 250mg", "generic_name": "Erythromycin Stearate", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Epival 250mg", "generic_name": "Sodium Valproate", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Epival 500mg", "generic_name": "Sodium Valproate", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Eltroxin 50mcg", "generic_name": "Thyroxine Sodium", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Exforge", "generic_name": "Amlodipine + Valsartan", "brand": "Novartis", "category": "TABLET", "manufacturer": "Novartis Pakistan"},

        # F
        {"name": "Flagyl 200mg", "generic_name": "Metronidazole", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Flagyl 400mg", "generic_name": "Metronidazole", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Flagyl Suspension", "generic_name": "Metronidazole", "brand": "Sanofi", "category": "SYRUP", "manufacturer": "Sanofi-Aventis"},
        {"name": "Fefol Vit", "generic_name": "Iron + Folic Acid + Vit C", "brand": "GSK", "category": "CAPSULE", "manufacturer": "GSK Pharma"},
        {"name": "Famopsin 40mg", "generic_name": "Famotidine", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Fucidin Cream", "generic_name": "Fusidic Acid", "brand": "Leo", "category": "CREAM", "manufacturer": "Leo Pharma"},
        {"name": "Flexin 250mg", "generic_name": "Naproxen", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Fastum Gel", "generic_name": "Ketoprofen", "brand": "Menarini", "category": "CREAM", "manufacturer": "Menarini Group"},
        {"name": "Flixonase Spray", "generic_name": "Fluticasone Propionate", "brand": "GSK", "category": "DROPS", "manufacturer": "GSK Pharma"},

        # G
        {"name": "Glucophage 500mg", "generic_name": "Metformin HCl", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Glucophage 850mg", "generic_name": "Metformin HCl", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Glucophage 1000mg", "generic_name": "Metformin HCl", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Getryl 1mg", "generic_name": "Glimepiride", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Getryl 2mg", "generic_name": "Glimepiride", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Getryl 3mg", "generic_name": "Glimepiride", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Getryl 4mg", "generic_name": "Glimepiride", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Gaviscon Liquid", "generic_name": "Sodium Alginate + Potassium Bicarbonate", "brand": "Reckitt", "category": "SYRUP", "manufacturer": "Reckitt Benckiser"},
        {"name": "Gaviscon Advance", "generic_name": "Sodium Alginate + Potassium Bicarbonate", "brand": "Reckitt", "category": "SYRUP", "manufacturer": "Reckitt Benckiser"},
        {"name": "Gravinate 50mg", "generic_name": "Dimenhydrinate", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Gravinate Liquid", "generic_name": "Dimenhydrinate", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},
        {"name": "Garamycin Cream", "generic_name": "Gentamicin Sulfate", "brand": "Bayer", "category": "CREAM", "manufacturer": "Bayer Pakistan"},

        # H
        {"name": "Heptalac Syrup", "generic_name": "Lactulose", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},
        {"name": "Hydryllin Syrup", "generic_name": "Aminophylline + Diphenhydramine + Ammonium Chloride", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},
        {"name": "Hydryllin Sugar Free", "generic_name": "Aminophylline + Diphenhydramine", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},
        {"name": "Hiltonphos Syrup", "generic_name": "Vitamin B-Complex", "brand": "Hilton", "category": "SYRUP", "manufacturer": "Hilton Pharma"},
        {"name": "Herbesser 30mg", "generic_name": "Diltiazem HCl", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},

        # I
        {"name": "Imuran 50mg", "generic_name": "Azathioprine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Inderal 10mg", "generic_name": "Propranolol HCl", "brand": "AstraZeneca", "category": "TABLET", "manufacturer": "AstraZeneca"},
        {"name": "Inderal 40mg", "generic_name": "Propranolol HCl", "brand": "AstraZeneca", "category": "TABLET", "manufacturer": "AstraZeneca"},
        {"name": "Ibra 20mg", "generic_name": "Rabaloc Esomeprazole", "brand": "Hilton", "category": "CAPSULE", "manufacturer": "Hilton Pharma"},
        {"name": "Iberet Folic-500", "generic_name": "Iron + Vit C + B-Complex + Folic Acid", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},

        # J
        {"name": "Januvia 50mg", "generic_name": "Sitagliptin", "brand": "MSD", "category": "TABLET", "manufacturer": "Merck Sharp & Dohme"},
        {"name": "Januvia 100mg", "generic_name": "Sitagliptin", "brand": "MSD", "category": "TABLET", "manufacturer": "Merck Sharp & Dohme"},
        {"name": "Janumet 50/500mg", "generic_name": "Sitagliptin + Metformin", "brand": "MSD", "category": "TABLET", "manufacturer": "Merck Sharp & Dohme"},
        {"name": "Janumet 50/1000mg", "generic_name": "Sitagliptin + Metformin", "brand": "MSD", "category": "TABLET", "manufacturer": "Merck Sharp & Dohme"},

        # K
        {"name": "Klaricid 250mg", "generic_name": "Clarithromycin", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Klaricid 500mg", "generic_name": "Clarithromycin", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Klaricid XL 500mg", "generic_name": "Clarithromycin", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Klaricid Syrup", "generic_name": "Clarithromycin", "brand": "Abbott", "category": "SYRUP", "manufacturer": "Abbott Laboratories"},
        {"name": "Kestine 10mg", "generic_name": "Ebastine", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Kalvol 500mg", "generic_name": "Paracetamol", "brand": "Atco", "category": "TABLET", "manufacturer": "Atco Laboratories"},
        {"name": "Ketof Drops", "generic_name": "Ketotifen Fumarate", "brand": "Novartis", "category": "DROPS", "manufacturer": "Novartis Pakistan"},

        # L
        {"name": "Loprin 75mg", "generic_name": "Aspirin", "brand": "Highnoon", "category": "TABLET", "manufacturer": "Highnoon Laboratories"},
        {"name": "Lipiget 10mg", "generic_name": "Atorvastatin Calcium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Lipiget 20mg", "generic_name": "Atorvastatin Calcium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Lipiget 40mg", "generic_name": "Atorvastatin Calcium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Lasix 40mg", "generic_name": "Furosemide", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Lasix Injection", "generic_name": "Furosemide", "brand": "Sanofi", "category": "INJECTION", "manufacturer": "Sanofi-Aventis"},
        {"name": "Lowplat 75mg", "generic_name": "Clopidogrel Bisulfate", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Linospan 600mg", "generic_name": "Linezolid", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Lorin 10mg", "generic_name": "Loratadine", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},

        # M
        {"name": "Montika 5mg", "generic_name": "Montelukast Sodium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Montika 10mg", "generic_name": "Montelukast Sodium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Motilium 10mg", "generic_name": "Domperidone", "brand": "Janssen", "category": "TABLET", "manufacturer": "Janssen Pharma"},
        {"name": "Motilium Suspension", "generic_name": "Domperidone", "brand": "Janssen", "category": "SYRUP", "manufacturer": "Janssen Pharma"},
        {"name": "Mobic 15mg", "generic_name": "Meloxicam", "brand": "Boehringer", "category": "TABLET", "manufacturer": "Boehringer Ingelheim"},
        {"name": "Maxolon 10mg", "generic_name": "Metoclopramide HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Maxolon Syrup", "generic_name": "Metoclopramide HCl", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Myteka 10mg", "generic_name": "Montelukast Sodium", "brand": "Hilton", "category": "TABLET", "manufacturer": "Hilton Pharma"},
        {"name": "Mucaine Suspension", "generic_name": "Oxetacaine + Aluminium Hydroxide", "brand": "Wyeth", "category": "SYRUP", "manufacturer": "Wyeth Pakistan"},

        # N
        {"name": "Norvasc 5mg", "generic_name": "Amlodipine Besylate", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Norvasc 10mg", "generic_name": "Amlodipine Besylate", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Neurobion Tablet", "generic_name": "Vitamin B1 + B6 + B12", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Neurobion Injection", "generic_name": "Vitamin B1 + B6 + B12", "brand": "Merck", "category": "INJECTION", "manufacturer": "Merck Pakistan"},
        {"name": "Nimesulide 100mg", "generic_name": "Nimesulide", "brand": "Sami", "category": "TABLET", "manufacturer": "Sami Pharmaceuticals"},
        {"name": "Nexum 20mg", "generic_name": "Esomeprazole Magnesium", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Nexum 40mg", "generic_name": "Esomeprazole Magnesium", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Nofec 50mg", "generic_name": "Diclofenac Potassium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "No-Spa 40mg", "generic_name": "Drotaverine HCl", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},

        # O
        {"name": "Optimax 100mg", "generic_name": "Sertraline HCl", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Osnate-D", "generic_name": "Calcium + Vitamin D", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Osnate Suspension", "generic_name": "Calcium Carbonate", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},
        {"name": "Omeez 20mg", "generic_name": "Omeprazole", "brand": "Highnoon", "category": "CAPSULE", "manufacturer": "Highnoon Laboratories"},
        {"name": "Orlistat 120mg", "generic_name": "Orlistat", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Otrivin Drops", "generic_name": "Xylometazoline HCl", "brand": "GSK", "category": "DROPS", "manufacturer": "GSK Pharma"},

        # P
        {"name": "Panadol 500mg", "generic_name": "Paracetamol", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Panadol Extra", "generic_name": "Paracetamol + Caffeine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Panadol CF", "generic_name": "Paracetamol + Pseudoephedrine + Chlorpheniramine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Ponstan 250mg", "generic_name": "Mefenamic Acid", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Ponstan Forte 500mg", "generic_name": "Mefenamic Acid", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Polyfax Skin Ointment", "generic_name": "Polymyxin B + Bacitracin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Polyfax Eye Ointment", "generic_name": "Polymyxin B + Bacitracin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Prothiaden 75mg", "generic_name": "Dosulepin HCl", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Plavix 75mg", "generic_name": "Clopidogrel", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Pulmonol Syrup", "generic_name": "Cough formulation", "brand": "CCL", "category": "SYRUP", "manufacturer": "CCL Pharmaceuticals"},
        {"name": "Pyodine Solution", "generic_name": "Povidone Iodine", "brand": "Brookes", "category": "DROPS", "manufacturer": "Brookes Pharma"},

        # Q
        {"name": "Quench Cream", "generic_name": "Silver Sulfadiazine", "brand": "Atco", "category": "CREAM", "manufacturer": "Atco Laboratories"},
        {"name": "Qalsan-D", "generic_name": "Calcium + Vitamin D3", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Qubol 100mg", "generic_name": "Coenzyme Q10", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},

        # R
        {"name": "Risek 20mg", "generic_name": "Omeprazole", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Risek 40mg", "generic_name": "Omeprazole", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Risek Insta 20mg", "generic_name": "Omeprazole", "brand": "Getz", "category": "SACHET", "manufacturer": "Getz Pharma"},
        {"name": "Rigix 10mg", "generic_name": "Cetirizine HCl", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Rigix Syrup", "generic_name": "Cetirizine HCl", "brand": "Getz", "category": "SYRUP", "manufacturer": "Getz Pharma"},
        {"name": "Rivotril 0.5mg", "generic_name": "Clonazepam", "brand": "Roche", "category": "TABLET", "manufacturer": "Roche Pakistan"},
        {"name": "Rivotril 2mg", "generic_name": "Clonazepam", "brand": "Roche", "category": "TABLET", "manufacturer": "Roche Pakistan"},
        {"name": "Rocephin 1g", "generic_name": "Ceftriaxone Sodium", "brand": "Roche", "category": "INJECTION", "manufacturer": "Roche Pakistan"},
        {"name": "Ritalin 10mg", "generic_name": "Methylphenidate HCl", "brand": "Novartis", "category": "TABLET", "manufacturer": "Novartis Pakistan"},

        # S
        {"name": "Surbex-Z", "generic_name": "Zinc + Vitamin B-Complex + Vitamin C", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Sangobion Capsule", "generic_name": "Iron + Vitamin B12 + Folic Acid", "brand": "Merck", "category": "CAPSULE", "manufacturer": "Merck Pakistan"},
        {"name": "Solu-Medrol 40mg", "generic_name": "Methylprednisolone", "brand": "Pfizer", "category": "INJECTION", "manufacturer": "Pfizer Pakistan"},
        {"name": "Solu-Medrol 500mg", "generic_name": "Methylprednisolone", "brand": "Pfizer", "category": "INJECTION", "manufacturer": "Pfizer Pakistan"},
        {"name": "Synflex 250mg", "generic_name": "Naproxen Sodium", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Synflex 550mg", "generic_name": "Naproxen Sodium", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Secnid 1g", "generic_name": "Secnidazole", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Spasler Neo", "generic_name": "Alverine Citrate", "brand": "Highnoon", "category": "TABLET", "manufacturer": "Highnoon Laboratories"},
        {"name": "Softin 10mg", "generic_name": "Loratadine", "brand": "Hilton", "category": "TABLET", "manufacturer": "Hilton Pharma"},
        {"name": "Sancos Syrup", "generic_name": "Sancos Antitussive", "brand": "Sandoz", "category": "SYRUP", "manufacturer": "Sandoz Pakistan"},

        # T
        {"name": "Tramal 50mg", "generic_name": "Tramadol Hydrochloride", "brand": "Searle", "category": "CAPSULE", "manufacturer": "Searle Pakistan"},
        {"name": "Tramal 100mg SR", "generic_name": "Tramadol Hydrochloride", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Tegral 200mg", "generic_name": "Carbamazepine", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Tenormin 50mg", "generic_name": "Atenolol", "brand": "AstraZeneca", "category": "TABLET", "manufacturer": "AstraZeneca"},
        {"name": "Tenormin 100mg", "generic_name": "Atenolol", "brand": "AstraZeneca", "category": "TABLET", "manufacturer": "AstraZeneca"},
        {"name": "T-Day 10mg", "generic_name": "Levocetirizine", "brand": "Hilton", "category": "TABLET", "manufacturer": "Hilton Pharma"},
        {"name": "Timo Tablet", "generic_name": "Tiemonium Methylsulfate", "brand": "Sami", "category": "TABLET", "manufacturer": "Sami Pharmaceuticals"},
        {"name": "Tres-Orix Syrup", "generic_name": "Lysine + Vitamin B-Complex + Cyproheptadine", "brand": "Brookes", "category": "SYRUP", "manufacturer": "Brookes Pharma"},

        # U
        {"name": "Ulsanic 1g", "generic_name": "Sucralfate", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Urispas 200mg", "generic_name": "Flavoxate HCl", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Unasyn 375mg", "generic_name": "Sultamicillin Tosylate", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},

        # V
        {"name": "Ventolin Inhaler", "generic_name": "Salbutamol", "brand": "GSK", "category": "INHALER", "manufacturer": "GSK Pharma"},
        {"name": "Ventolin Syrup", "generic_name": "Salbutamol", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Ventolin Nebules", "generic_name": "Salbutamol", "brand": "GSK", "category": "DROPS", "manufacturer": "GSK Pharma"},
        {"name": "Voren 50mg", "generic_name": "Diclofenac Sodium", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Valium 5mg", "generic_name": "Diazepam", "brand": "Roche", "category": "TABLET", "manufacturer": "Roche Pakistan"},
        {"name": "Valium 10mg", "generic_name": "Diazepam", "brand": "Roche", "category": "TABLET", "manufacturer": "Roche Pakistan"},
        {"name": "Voltral 50mg", "generic_name": "Diclofenac Sodium", "brand": "Novartis", "category": "TABLET", "manufacturer": "Novartis Pakistan"},
        {"name": "Voltral Emulgel", "generic_name": "Diclofenac Sodium", "brand": "Novartis", "category": "CREAM", "manufacturer": "Novartis Pakistan"},
        {"name": "Vermox 100mg", "generic_name": "Mebendazole", "brand": "Janssen", "category": "TABLET", "manufacturer": "Janssen Pharma"},
        {"name": "Vermox Suspension", "generic_name": "Mebendazole", "brand": "Janssen", "category": "SYRUP", "manufacturer": "Janssen Pharma"},

        # W
        {"name": "Wintogeno Cream", "generic_name": "Methyl Salicylate", "brand": "Reckitt", "category": "CREAM", "manufacturer": "Reckitt Benckiser"},
        {"name": "Wymox 250mg", "generic_name": "Amoxicillin", "brand": "Wyeth", "category": "CAPSULE", "manufacturer": "Wyeth Pakistan"},
        {"name": "Warfarin 5mg", "generic_name": "Warfarin Sodium", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},

        # X
        {"name": "Xanax 0.25mg", "generic_name": "Alprazolam", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Xanax 0.5mg", "generic_name": "Alprazolam", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Xanax 1mg", "generic_name": "Alprazolam", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Xola 20mg", "generic_name": "Esomeprazole Magnesium", "brand": "Searle", "category": "CAPSULE", "manufacturer": "Searle Pakistan"},
        {"name": "Xalatan Eye Drops", "generic_name": "Latanoprost", "brand": "Pfizer", "category": "DROPS", "manufacturer": "Pfizer Pakistan"},

        # Y
        {"name": "Yasmin Tablet", "generic_name": "Drospirenone + Ethinyl Estradiol", "brand": "Bayer", "category": "TABLET", "manufacturer": "Bayer Pakistan"},
        {"name": "Yomax 10mg", "generic_name": "Yohimbine", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},

        # Z
        {"name": "Zantac 150mg", "generic_name": "Ranitidine HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Zantac 300mg", "generic_name": "Ranitidine HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Zyrtec 10mg", "generic_name": "Cetirizine HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Zyrtec Syrup", "generic_name": "Cetirizine HCl", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Zibac 500mg", "generic_name": "Azithromycin", "brand": "Sami", "category": "TABLET", "manufacturer": "Sami Pharmaceuticals"},
        {"name": "Zovirax Cream", "generic_name": "Acyclovir", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Zecuf Syrup", "generic_name": "Herbal cough formula", "brand": "JB", "category": "SYRUP", "manufacturer": "JB Chemicals"}
    ]
    
    count = 0
    today = datetime.date.today()
    one_year_later = today + datetime.timedelta(days=365)
    
    for med_data in STANDARD_MEDICINES:
        # Check if already exists in this hospital
        exists = Medicine.all_objects.filter(
            name=med_data["name"],
            brand=med_data["brand"],
            hospital=request.user.hospital
        ).exists()
        
        if not exists:
            try:
                Medicine.objects.create(
                    name=med_data["name"],
                    generic_name=med_data["generic_name"],
                    brand=med_data["brand"],
                    manufacturer=med_data["manufacturer"],
                    category=med_data["category"],
                    price=Decimal("0.00"),
                    wholesale_price=Decimal("0.00"),
                    quantity=0,
                    expiry_date=one_year_later,
                    is_active=True,
                    hospital=request.user.hospital
                )
                count += 1
            except IntegrityError:
                pass
                
    if count > 0:
        messages.success(request, f"Successfully loaded {count} standard medicines. You can now configure their quantities, prices and batches.")
    else:
        messages.info(request, "All standard medicines are already loaded in your inventory.")
        
    return redirect('medicine_list')