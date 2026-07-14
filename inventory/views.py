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
    from datetime import timedelta
    from django.db.models import Sum
    from billing.models import Invoice, Expense
    from sales.models import Sale

    low_stock = Medicine.objects.low_stock()
    expiring = Medicine.objects.expiring_soon(30)
    
    # Financial Analytics (Last 7 Days)
    today = timezone.localdate()
    seven_days_ago = today - timedelta(days=6)
    
    labels = []
    daily_income = []
    daily_expense = []
    
    for i in range(7):
        day = seven_days_ago + timedelta(days=i)
        labels.append(day.strftime("%a %d"))
        
        # Calculate income
        day_inv = Invoice.objects.filter(created_at__date=day).aggregate(s=Sum('paid'))['s'] or 0
        day_sale = Sale.objects.filter(created_at__date=day, is_returned=False).aggregate(s=Sum('paid'))['s'] or 0
        daily_income.append(float(day_inv + day_sale))
        
        # Calculate expenses
        day_exp = Expense.objects.filter(date=day).aggregate(s=Sum('amount'))['s'] or 0
        daily_expense.append(float(day_exp))
        
    # Calculate 30-day summaries
    thirty_days_ago = today - timedelta(days=29)
    tot_inv_30d = Invoice.objects.filter(created_at__date__gte=thirty_days_ago).aggregate(s=Sum('paid'))['s'] or 0
    tot_sale_30d = Sale.objects.filter(created_at__date__gte=thirty_days_ago, is_returned=False).aggregate(s=Sum('paid'))['s'] or 0
    total_income_30d = float(tot_inv_30d + tot_sale_30d)
    
    total_expense_30d = float(Expense.objects.filter(date__gte=thirty_days_ago).aggregate(s=Sum('amount'))['s'] or 0)
    
    return render(request, 'dashboard.html', {
        'low_stock': low_stock,
        'expiring': expiring,
        'today': today,
        'finance_labels': json.dumps(labels),
        'finance_income': json.dumps(daily_income),
        'finance_expense': json.dumps(daily_expense),
        'total_income_30d': total_income_30d,
        'total_expense_30d': total_expense_30d,
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

    # Essential medicine catalog based on 20+ years of pharmacist expertise
    STANDARD_MEDICINES = [
        # Analgesics & Antipyretics
        {"name": "Panadol 500mg", "generic_name": "Paracetamol", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Panadol Extra", "generic_name": "Paracetamol + Caffeine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Brufen 400mg", "generic_name": "Ibuprofen", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Ponstan 250mg", "generic_name": "Mefenamic Acid", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Disprin 300mg", "generic_name": "Aspirin", "brand": "Reckitt", "category": "TABLET", "manufacturer": "Reckitt Benckiser"},
        {"name": "Tramal 50mg", "generic_name": "Tramadol Hydrochloride", "brand": "Searle", "category": "CAPSULE", "manufacturer": "Searle Pakistan"},
        {"name": "Calpol Suspension", "generic_name": "Paracetamol", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        
        # Antibiotics & Antivirals
        {"name": "Augmentin 625mg", "generic_name": "Co-amoxiclav", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Augmentin DS Suspension", "generic_name": "Co-amoxiclav", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Amoxil 500mg", "generic_name": "Amoxicillin", "brand": "GSK", "category": "CAPSULE", "manufacturer": "GSK Pharma"},
        {"name": "Flagyl 400mg", "generic_name": "Metronidazole", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Flagyl Suspension", "generic_name": "Metronidazole", "brand": "Sanofi", "category": "SYRUP", "manufacturer": "Sanofi-Aventis"},
        {"name": "Ciproxin 500mg", "generic_name": "Ciprofloxacin", "brand": "Bayer", "category": "TABLET", "manufacturer": "Bayer Pakistan"},
        {"name": "Klaricid 250mg", "generic_name": "Clarithromycin", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Azomax 500mg", "generic_name": "Azithromycin", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Ceclor 250mg", "generic_name": "Cefaclor", "brand": "Eli Lilly", "category": "CAPSULE", "manufacturer": "Eli Lilly"},
        
        # Gastrointestinal
        {"name": "Risek 20mg", "generic_name": "Omeprazole", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Risek 40mg", "generic_name": "Omeprazole", "brand": "Getz", "category": "CAPSULE", "manufacturer": "Getz Pharma"},
        {"name": "Zantac 150mg", "generic_name": "Ranitidine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Gravinate 50mg", "generic_name": "Dimenhydrinate", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Gaviscon Liquid", "generic_name": "Sodium Alginate + Potassium Bicarbonate", "brand": "Reckitt", "category": "SYRUP", "manufacturer": "Reckitt Benckiser"},
        {"name": "Entamizole", "generic_name": "Diloxanide Furoate + Metronidazole", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Colofac 135mg", "generic_name": "Mebeverine HCl", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Heptalac Syrup", "generic_name": "Lactulose", "brand": "Searle", "category": "SYRUP", "manufacturer": "Searle Pakistan"},

        # Cardiovascular & Blood Pressure
        {"name": "Loprin 75mg", "generic_name": "Aspirin", "brand": "Highnoon", "category": "TABLET", "manufacturer": "Highnoon Laboratories"},
        {"name": "Capoten 25mg", "generic_name": "Captopril", "brand": "Bristol-Myers", "category": "TABLET", "manufacturer": "Bristol-Myers Squibb"},
        {"name": "Concor 5mg", "generic_name": "Bisoprolol Fumarate", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Norvasc 5mg", "generic_name": "Amlodipine Besylate", "brand": "Pfizer", "category": "TABLET", "manufacturer": "Pfizer Pakistan"},
        {"name": "Lipiget 10mg", "generic_name": "Atorvastatin Calcium", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Angised 0.5mg", "generic_name": "Glyceryl Trinitrate", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},

        # Antidiabetic
        {"name": "Glucophage 500mg", "generic_name": "Metformin HCl", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},
        {"name": "Getryl 2mg", "generic_name": "Glimepiride", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Diamicron 60mg MR", "generic_name": "Gliclazide", "brand": "Servier", "category": "TABLET", "manufacturer": "Servier Research"},

        # Respiratory & Allergy
        {"name": "Panadol CF", "generic_name": "Paracetamol + Pseudoephedrine + Chlorpheniramine", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Arinac", "generic_name": "Ibuprofen + Pseudoephedrine", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Kestine 10mg", "generic_name": "Ebastine", "brand": "Searle", "category": "TABLET", "manufacturer": "Searle Pakistan"},
        {"name": "Zyrtec 10mg", "generic_name": "Cetirizine HCl", "brand": "GSK", "category": "TABLET", "manufacturer": "GSK Pharma"},
        {"name": "Ventolin Inhaler", "generic_name": "Salbutamol", "brand": "GSK", "category": "INHALER", "manufacturer": "GSK Pharma"},
        {"name": "Ventolin Syrup", "generic_name": "Salbutamol", "brand": "GSK", "category": "SYRUP", "manufacturer": "GSK Pharma"},
        {"name": "Acefyl Syrup", "generic_name": "Acefylline Piperazine", "brand": "Herbion", "category": "SYRUP", "manufacturer": "Herbion Pakistan"},
        {"name": "Rigix 10mg", "generic_name": "Cetirizine", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},
        {"name": "Montika 10mg", "generic_name": "Montelukast", "brand": "Getz", "category": "TABLET", "manufacturer": "Getz Pharma"},

        # Hormones / Steroids / Vitamins
        {"name": "Decadron 4mg", "generic_name": "Dexamethasone", "brand": "Organon", "category": "TABLET", "manufacturer": "Organon"},
        {"name": "Avil Tablet", "generic_name": "Pheniramine Maleate", "brand": "Sanofi", "category": "TABLET", "manufacturer": "Sanofi-Aventis"},
        {"name": "Surbex-Z", "generic_name": "Zinc + Vitamin B-Complex + Vitamin C", "brand": "Abbott", "category": "TABLET", "manufacturer": "Abbott Laboratories"},
        {"name": "Cac 1000 Plus", "generic_name": "Calcium + Vitamin C", "brand": "Sandoz", "category": "SACHET", "manufacturer": "Sandoz Pakistan"},
        {"name": "Evion 400mg", "generic_name": "Vitamin E", "brand": "Merck", "category": "CAPSULE", "manufacturer": "Merck Pakistan"},
        {"name": "Sangobion Capsule", "generic_name": "Iron + Vitamin B12 + Folic Acid", "brand": "Merck", "category": "CAPSULE", "manufacturer": "Merck Pakistan"},
        {"name": "Neurobion Tablet", "generic_name": "Vitamin B1, B6, B12", "brand": "Merck", "category": "TABLET", "manufacturer": "Merck Pakistan"},

        # Creams & Topical
        {"name": "Polyfax Skin Ointment", "generic_name": "Polymyxin B + Bacitracin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Polyfax Eye Ointment", "generic_name": "Polymyxin B + Bacitracin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Betnovate-N Cream", "generic_name": "Betamethasone Valerate + Neomycin", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Pyodine Solution", "generic_name": "Povidone-Iodine", "brand": "Brookes", "category": "DROPS", "manufacturer": "Brookes Pharma"},
        {"name": "Betnovate-C Cream", "generic_name": "Betamethasone Valerate + Clioquinol", "brand": "GSK", "category": "CREAM", "manufacturer": "GSK Pharma"},
        {"name": "Daktarin Cream", "generic_name": "Miconazole Nitrate", "brand": "Janssen", "category": "CREAM", "manufacturer": "Janssen Pharma"}
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