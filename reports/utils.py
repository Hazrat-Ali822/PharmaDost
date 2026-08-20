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


# ---------------------------------------------------------------------------
# Per-module profit
# ---------------------------------------------------------------------------

def module_profit_data(start, end):
    """Revenue, cost and profit for each module, for a date range.

    **Basis is billed, not cash collected.** Profit only means anything when the
    cost is matched to the sale that incurred it, so a bill raised in the period
    counts in the period even if the patient pays later. That is also what the
    existing Profit Report does; the *dashboard* is deliberately the other way
    (cash), and the screen says so, because two numbers that differ with nothing
    on the page to explain why is how this system has misled people before.

    Cost is only recorded in two places, and the report is explicit about the
    rest rather than presenting an unrecorded cost as 100% margin:

      * **Pharmacy** — `SaleItem.cost_price`, the batch COGS frozen at the moment
        of sale, so a later price change cannot rewrite an old sale's profit.
      * **Lab** — `LabTest.cost_price`, entered by the admin on the price list.
      * **OT** and **Ambulance** — `cost_price` frozen onto the record at
        scheduling / dispatch, so repricing the catalogue or the fleet later
        cannot rewrite an old margin.
      * **OPD** — the doctor's own share of the consultation fee
        (`Doctor.share_percent`). That share *is* the hospital's cost of seeing
        the patient. Read live rather than frozen, because it is a standing
        arrangement rather than a per-visit figure.

    Every other module reports revenue with `cost_tracked = False`.

    Each row: key, label, revenue, cost, profit, cost_tracked, note.
    """
    from decimal import Decimal as D

    from billing import revenue as rev
    from billing.models import InvoiceItem
    from lab.models import TestResult
    from sales.models import Sale, SaleItem

    zero = D('0.00')

    # --- Pharmacy: its own ledger, never invoiced, so it cannot double count.
    sales = Sale.objects.filter(created_at__date__range=(start, end),
                                is_returned=False)
    ph_revenue = sales.aggregate(s=Sum('total'))['s'] or zero
    ph_items = SaleItem.objects.filter(sale__in=sales)
    money = DecimalField(max_digits=14, decimal_places=2)
    ph_cost = (ph_items
               .filter(cost_price__gt=0)
               .aggregate(s=Sum(ExpressionWrapper(F('cost_price') * F('quantity'),
                                                  output_field=money)))['s'] or zero)
    # Sales whose purchase price nobody recorded. Their cost is UNKNOWN, not
    # zero — but the subtraction cannot tell the difference, so they arrive in
    # the profit column as 100% margin and the owner has no way to see it. The
    # figure is reported next to the row instead of being quietly absorbed.
    ph_cost_gap = (ph_items
                   .filter(cost_price__lte=0)
                   .aggregate(s=Sum(ExpressionWrapper(
                       F('unit_price') * F('quantity') - F('discount'),
                       output_field=money)))['s'] or zero)
    # What those unpriced sales WOULD have cost at the medicine's purchase price
    # as it stands today. Offered as an estimate and never stored: the real cost
    # of a tablet sold three months ago was not written down and cannot be
    # recovered, so writing this into `SaleItem.cost_price` would turn a guess
    # into a record. Shown beside the gap so an owner can judge the old months
    # without the report pretending to know.
    ph_cost_gap_estimate = (ph_items
                            .filter(cost_price__lte=0, medicine__cost_price__gt=0)
                            .aggregate(s=Sum(ExpressionWrapper(
                                F('medicine__cost_price') * F('quantity'),
                                output_field=money)))['s'] or zero)
    # The other half of the old defect, and the one nothing could see: before
    # `add_stock` stopped defaulting an unknown cost to the SELLING price, every
    # batch stocked that way produced sale lines whose cost equals their price.
    # Those look tracked and report exactly zero profit. A pharmacy does not
    # sell at cost line after line, so the count is worth surfacing — but it is
    # reported, never corrected, for the same reason as above.
    ph_zero_margin = (ph_items
                      .filter(unit_price__gt=0, cost_price__gte=F('unit_price'))
                      .aggregate(s=Sum(ExpressionWrapper(
                          F('unit_price') * F('quantity') - F('discount'),
                          output_field=money)))['s'] or zero)

    # --- Everything else comes off the invoices, classified by description.
    items = (InvoiceItem.objects
             .filter(invoice__status='ACTIVE',
                     invoice__created_at__date__range=(start, end))
             .select_related('invoice', 'invoice__appointment',
                             'invoice__appointment__doctor'))

    billed = {}
    opd_cost = zero
    for item in items:
        kind = rev.classify(item.description)
        billed[kind] = billed.get(kind, zero) + (item.amount or zero)
        if kind == rev.OPD:
            doctor = getattr(getattr(item.invoice, 'appointment', None), 'doctor', None)
            if doctor is not None and doctor.share_percent is not None:
                opd_cost += ((item.amount or zero) * D(doctor.share_percent)
                             / D('100')).quantize(D('0.01'))

    # --- Lab cost: the reagent went out of the door whether or not the bill was
    # later voided, so it is counted off the tests themselves.
    lab_cost = (TestResult.objects
                .filter(is_cancelled=False,
                        test_order__order_date__date__range=(start, end))
                .aggregate(s=Sum('lab_test__cost_price'))['s'] or zero)

    # --- OT cost: frozen onto the record at scheduling, so repricing the
    # procedure catalogue later cannot rewrite an old operation's margin.
    # Dated by the **invoice**, so cost and revenue land in the same period —
    # an operation scheduled for next week but billed today otherwise split
    # itself across two months. Records predating that link (and any whose
    # every charge was zero, so no invoice was raised) fall back to the
    # operation date, which is the best that can be said about them.
    from django.db.models import Q

    from ot.models import SurgeryRecord
    ot_cost = (SurgeryRecord.objects
               .filter(Q(invoice__created_at__date__range=(start, end))
                       | Q(invoice__isnull=True,
                           start_time__date__range=(start, end)))
               .aggregate(s=Sum('cost_price'))['s'] or zero)

    rows = [{
        'key': 'PHARMACY',
        'label': 'Pharmacy',
        'revenue': ph_revenue,
        'cost': ph_cost,
        'cost_tracked': True,
        'cost_gap': ph_cost_gap,
        'cost_gap_estimate': ph_cost_gap_estimate,
        'zero_margin': ph_zero_margin,
        'note': ('Batch cost frozen at the time of each sale.' if not ph_cost_gap else
                 f'Includes {ph_cost_gap} of sales with no purchase price recorded, '
                 f'counted at zero cost — the profit shown is higher than the truth. '
                 f'Set the purchase price on those medicines.'),
    }]

    # --- Ambulance cost: frozen onto each trip at dispatch, same rule as OT.
    # Dated by the invoice where there is one — a trip completed on the 31st but
    # billed on the 1st otherwise puts its cost and its revenue in different
    # months. A trip with no patient raises no invoice (see ambulance.services)
    # and falls back to the call time.
    from ambulance.models import AmbulanceTrip
    amb_cost = (AmbulanceTrip.objects
                .filter(status=AmbulanceTrip.STATUS_COMPLETED)
                .filter(Q(invoice__created_at__date__range=(start, end))
                        | Q(invoice__isnull=True,
                            called_at__date__range=(start, end)))
                .aggregate(s=Sum('cost_price'))['s'] or zero)

    # --- Imaging cost: film / contrast / gel from the scan price list, counted
    # off the studies themselves for the same reason lab is — the consumable
    # was used whether or not the bill was later voided.
    from imaging.models import ImagingStudy
    img_cost = (ImagingStudy.objects
                .exclude(status='Cancelled')
                .filter(Q(invoice__created_at__date__range=(start, end))
                        | Q(invoice__isnull=True,
                            study_date__date__range=(start, end)))
                .aggregate(s=Sum('cost_price'))['s'] or zero)

    # --- IPD cost: the pharmacy stock a ward dose actually consumed, frozen on
    # the log. Dated by the DISCHARGE invoice where there is one, because that
    # is where the revenue lands — a drug given on the 30th and billed at
    # discharge on the 2nd otherwise puts cost and revenue in different months.
    from ipd.models import MedicationLog
    ipd_cost = (MedicationLog.objects
                .filter(Q(admission__discharge_invoice__created_at__date__range=(start, end))
                        | Q(admission__discharge_invoice__isnull=True,
                            administered_at__date__range=(start, end)))
                .aggregate(s=Sum(ExpressionWrapper(F('cost_price') * F('quantity'),
                                                   output_field=money)))['s'] or zero)

    # --- Emergency / Maternity: consumables frozen on the case and on the
    # delivery, dated by their invoice like OT and ambulance.
    from emergency.models import EmergencyCase
    emg_cost = (EmergencyCase.objects
                .filter(Q(invoice__created_at__date__range=(start, end))
                        | Q(invoice__isnull=True,
                            created_at__date__range=(start, end)))
                .aggregate(s=Sum('cost_price'))['s'] or zero)

    from maternity.models import Delivery
    mat_cost = (Delivery.objects
                .filter(Q(invoice__created_at__date__range=(start, end))
                        | Q(invoice__isnull=True,
                            delivered_at__date__range=(start, end)))
                .aggregate(s=Sum('cost_price'))['s'] or zero)

    for key in (rev.OPD, rev.LAB, rev.IMAGING, rev.IPD, rev.OT,
                rev.EMERGENCY, rev.MATERNITY, rev.AMBULANCE, rev.OTHER):
        amount = billed.get(key, zero)
        if key == rev.LAB:
            cost, tracked = lab_cost, True
            note = ("Reagent / consumable cost from the lab price list."
                    if lab_cost else
                    "No cost entered yet — set it per test on the Lab Test Prices "
                    "screen and this row becomes a real margin.")
        elif key == rev.OPD:
            cost, tracked = opd_cost, True
            note = ("The doctor's share of the fee." if opd_cost else
                    "Every doctor keeps 100% of the fee, so the hospital's OPD "
                    "margin is nil. Set a share on the doctor's record to change that.")
        elif key == rev.OT:
            cost, tracked = ot_cost, True
            note = ("Theatre cost frozen on each operation." if ot_cost else
                    "No cost entered yet — set it per procedure on the Surgery "
                    "Procedures screen and this row becomes a real margin.")
        elif key == rev.AMBULANCE:
            cost, tracked = amb_cost, True
            note = ("Fuel / running cost frozen on each trip." if amb_cost else
                    "No cost entered yet — set 'cost per trip' on the vehicle in "
                    "Fleet & Drivers and this row becomes a real margin.")
        elif key == rev.IMAGING:
            cost, tracked = img_cost, True
            note = ("Film / contrast cost from the scan price list." if img_cost else
                    "No cost entered yet — set it per scan on the Scan Prices "
                    "screen and this row becomes a real margin.")
        elif key == rev.IPD:
            cost, tracked = ipd_cost, True
            note = ("Pharmacy stock consumed by ward doses." if ipd_cost else
                    "No medicine cost recorded yet — give ward doses from "
                    "pharmacy stock (and set those medicines' purchase price) "
                    "and this row becomes a real margin.")
        elif key == rev.EMERGENCY:
            cost, tracked = emg_cost, True
            note = ("Consumables recorded on each casualty case." if emg_cost else
                    "No cost entered yet — fill 'Consumables cost' when "
                    "registering a case and this row becomes a real margin.")
        elif key == rev.MATERNITY:
            cost, tracked = mat_cost, True
            note = ("Consumables recorded on each delivery." if mat_cost else
                    "No cost entered yet — fill 'Consumables cost' on the "
                    "delivery record and this row becomes a real margin.")
        else:
            cost, tracked, note = zero, False, 'Cost is not recorded for this module.'

        if amount == zero and cost == zero:
            continue                       # a module with no activity says nothing
        rows.append({
            'key': key, 'label': rev.LABELS[key], 'revenue': amount,
            'cost': cost, 'cost_tracked': tracked, 'note': note,
        })

    for row in rows:
        row['profit'] = row['revenue'] - row['cost']
        row['margin'] = (row['profit'] / row['revenue'] * 100) if row['revenue'] else None

    totals = {
        'revenue': sum((r['revenue'] for r in rows), zero),
        'cost': sum((r['cost'] for r in rows), zero),
    }
    totals['profit'] = totals['revenue'] - totals['cost']
    totals['partial'] = any(not r['cost_tracked'] and r['revenue'] for r in rows)
    # `partial` means the total is a FLOOR (a module whose cost is not recorded
    # at all is reported revenue-only). `overstated` is the opposite failure and
    # needs saying separately: the cost of these sales IS in the subtraction, as
    # zero, so the profit above is too high rather than too low.
    totals['cost_gap'] = sum((r.get('cost_gap') or zero for r in rows), zero)
    totals['overstated'] = totals['cost_gap'] > zero
    totals['cost_gap_estimate'] = sum(
        (r.get('cost_gap_estimate') or zero for r in rows), zero)
    # The profit those sales would show if valued at today's purchase prices.
    # An ESTIMATE, and labelled as one everywhere it appears.
    totals['estimated_profit'] = totals['profit'] - totals['cost_gap_estimate']
    totals['zero_margin'] = sum((r.get('zero_margin') or zero for r in rows), zero)

    # --- The bottom line the owner is actually asking for.
    # Everything above is GROSS profit: revenue minus the direct cost of
    # delivering it (the tablet, the reagent, the film, the doctor's share).
    # It is not what the hospital kept — rent, salaries, electricity and the
    # rest are recorded as Expenses and belong to no single module, so they
    # cannot be shown in the table without inventing an allocation. Subtracting
    # them once, at the end, is the honest way to reach net profit; leaving
    # them out entirely let a hospital read a healthy gross margin as money in
    # hand.
    from billing.models import Expense
    totals['expenses'] = (Expense.objects
                          .filter(date__range=(start, end))
                          .aggregate(s=Sum('amount'))['s'] or zero)
    totals['net_profit'] = totals['profit'] - totals['expenses']
    return rows, totals
