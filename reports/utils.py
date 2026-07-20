from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce


# ---------------------------------------------------------------------------
# Date range resolution (presets + custom)
# ---------------------------------------------------------------------------

def _parse(d):
    try:
        return date.fromisoformat(d)
    except (TypeError, ValueError):
        return None


def resolve_range(request):
    """Read GET params and return {start, end, preset, label}."""
    preset = request.GET.get('preset', 'today')
    today = date.today()

    if preset == 'today':
        start = end = today
        label = 'Today'
    elif preset == 'yesterday':
        start = end = today - timedelta(days=1)
        label = 'Yesterday'
    elif preset == 'week':
        start = today - timedelta(days=today.weekday())
        end = today
        label = 'This Week'
    elif preset == 'month':
        start = today.replace(day=1)
        end = today
        label = 'This Month'
    elif preset == 'custom':
        start = _parse(request.GET.get('from')) or today
        end = _parse(request.GET.get('to')) or today
        if end < start:
            start, end = end, start
        label = f'{start} to {end}'
    else:
        preset = 'today'
        start = end = today
        label = 'Today'

    return {'start': start, 'end': end, 'preset': preset, 'label': label}


# ---------------------------------------------------------------------------
# Sales report (uses STORED Sale totals, excludes returned sales)
# ---------------------------------------------------------------------------

def sales_report_data(start, end):
    from sales.models import Sale

    sales = Sale.objects.filter(created_at__date__range=(start, end), is_returned=False)

    agg = sales.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00')),
        paid=Coalesce(Sum('paid'), Decimal('0.00')),
        bills=Count('id'),
    )
    total = agg['total']
    paid = agg['paid']
    credit = total - paid

    by_type = {}
    for row in sales.values('sale_type').annotate(total=Coalesce(Sum('total'), Decimal('0.00')), bills=Count('id')):
        by_type[row['sale_type']] = {'total': row['total'], 'bills': row['bills']}

    by_payment = {}
    for row in sales.values('payment_method').annotate(total=Coalesce(Sum('total'), Decimal('0.00')), bills=Count('id')):
        by_payment[row['payment_method']] = {'total': row['total'], 'bills': row['bills']}

    return {
        'total': total,
        'paid': paid,
        'credit': credit,
        'bills': agg['bills'],
        'retail': by_type.get('RETAIL', {'total': Decimal('0.00'), 'bills': 0}),
        'wholesale': by_type.get('WHOLESALE', {'total': Decimal('0.00'), 'bills': 0}),
        'by_payment': by_payment,
    }


# ---------------------------------------------------------------------------
# Profit report (revenue = stored Sale.total ; cost = batch cost * qty)
# ---------------------------------------------------------------------------

def profit_report_data(start, end):
    from sales.models import Sale, SaleItem

    sales = (Sale.objects
             .filter(created_at__date__range=(start, end), is_returned=False)
             .prefetch_related('items', 'items__batch', 'items__medicine'))

    total_revenue = Decimal('0.00')
    total_cost = Decimal('0.00')
    by_type = {
        'RETAIL': {'revenue': Decimal('0.00'), 'cost': Decimal('0.00')},
        'WHOLESALE': {'revenue': Decimal('0.00'), 'cost': Decimal('0.00')},
    }
    item_profit = {}   # medicine name -> {qty, profit, revenue}

    for sale in sales:
        sale_cost = Decimal('0.00')
        for it in sale.items.all():
            # prefer the cost frozen on the line at sale time; fall back to the live
            # batch cost for rows created before COGS was captured on the sale item
            cost_each = it.cost_price if it.cost_price else (it.batch.cost_price if it.batch_id else Decimal('0.00'))
            cost = cost_each * it.quantity
            sale_cost += cost
            line_rev = it.line_total
            key = it.medicine.name
            rec = item_profit.setdefault(key, {'qty': 0, 'profit': Decimal('0.00'), 'revenue': Decimal('0.00')})
            rec['qty'] += it.quantity
            rec['profit'] += (line_rev - cost)
            rec['revenue'] += line_rev

        total_revenue += sale.total
        total_cost += sale_cost
        bucket = by_type.get(sale.sale_type)
        if bucket is not None:
            bucket['revenue'] += sale.total
            bucket['cost'] += sale_cost

    gross_profit = total_revenue - total_cost
    margin = (gross_profit / total_revenue * 100) if total_revenue else Decimal('0.00')

    for b in by_type.values():
        b['profit'] = b['revenue'] - b['cost']

    top_items = sorted(
        ({'name': k, **v} for k, v in item_profit.items()),
        key=lambda r: r['profit'], reverse=True
    )[:15]

    return {
        'revenue': total_revenue,
        'cost': total_cost,
        'gross_profit': gross_profit,
        'margin': margin,
        'by_type': by_type,
        'top_items': top_items,
    }


# ---------------------------------------------------------------------------
# Day Book — daily cash view: income (sales + invoice collections) vs expenses
# ---------------------------------------------------------------------------

def daybook_data(start, end):
    from sales.models import Sale
    from billing.models import Invoice, Expense

    # --- pharmacy sales (exclude returned); "collected" = amount actually paid
    sales = Sale.objects.filter(created_at__date__range=(start, end), is_returned=False)
    s_agg = sales.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00')),
        collected=Coalesce(Sum('paid'), Decimal('0.00')),
        bills=Count('id'),
    )
    sales_total = s_agg['total']
    sales_collected = s_agg['collected']
    sales_credit = sales_total - sales_collected

    sales_by_method = {}
    for row in sales.values('payment_method').annotate(
            collected=Coalesce(Sum('paid'), Decimal('0.00'))):
        sales_by_method[row['payment_method']] = row['collected']

    # --- service / OPD invoices raised in the period
    invoices = Invoice.objects.filter(created_at__date__range=(start, end))
    i_agg = invoices.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00')),
        collected=Coalesce(Sum('paid'), Decimal('0.00')),
        count=Count('id'),
    )
    inv_total = i_agg['total']
    inv_collected = i_agg['collected']
    inv_outstanding = inv_total - inv_collected

    # --- expenses
    expenses = Expense.objects.filter(date__range=(start, end))
    exp_total = expenses.aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t']
    cat_labels = dict(Expense.CATEGORY_CHOICES)
    exp_by_cat = {}
    for row in expenses.values('category').annotate(t=Coalesce(Sum('amount'), Decimal('0.00'))):
        exp_by_cat[cat_labels.get(row['category'], row['category'])] = row['t']

    total_income = sales_collected + inv_collected
    net_cash = total_income - exp_total
    receivable_total = sales_credit + inv_outstanding

    return {
        'sales_total': sales_total,
        'sales_collected': sales_collected,
        'sales_credit': sales_credit,
        'sales_bills': s_agg['bills'],
        'sales_by_method': sales_by_method,
        'inv_total': inv_total,
        'inv_collected': inv_collected,
        'inv_outstanding': inv_outstanding,
        'inv_count': i_agg['count'],
        'exp_total': exp_total,
        'exp_by_cat': exp_by_cat,
        'total_income': total_income,
        'net_cash': net_cash,
        'receivable_total': receivable_total,
    }


# ---------------------------------------------------------------------------
# Inventory report (valuation + status buckets)
# ---------------------------------------------------------------------------

def inventory_snapshot():
    from inventory.models import Medicine, StockBatch

    items = list(Medicine.objects.values(
        'id', 'name', 'generic_name', 'brand', 'category',
        'price', 'wholesale_price', 'reorder_level', 'quantity', 'expiry_date'
    ))

    today = date.today()
    soon = today + timedelta(days=30)

    # stock valuation at cost (from batches)
    cost_val = StockBatch.objects.filter(quantity__gt=0).aggregate(
        v=Coalesce(Sum(ExpressionWrapper(F('quantity') * F('cost_price'),
                                         output_field=DecimalField(max_digits=16, decimal_places=2))),
                   Decimal('0.00')))['v']

    # retail valuation
    retail_val = Medicine.objects.aggregate(
        v=Coalesce(Sum(ExpressionWrapper(F('quantity') * F('price'),
                                         output_field=DecimalField(max_digits=16, decimal_places=2))),
                   Decimal('0.00')))['v']

    low = Medicine.objects.low_stock().count()
    expired = Medicine.objects.filter(is_active=True, expiry_date__lt=today).count()
    near_expiry = Medicine.objects.filter(is_active=True, expiry_date__range=(today, soon)).count()

    summary = {
        'products': len(items),
        'cost_value': cost_val,
        'retail_value': retail_val,
        'potential_profit': retail_val - cost_val,
        'low_stock': low,
        'expired': expired,
        'near_expiry': near_expiry,
    }
    return items, summary
