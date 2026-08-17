from django.shortcuts import render
from accounts.decorators import role_required, feature_required
from .export import csv_response, wants_csv
from .utils import (resolve_range, sales_report_data, profit_report_data,
                    inventory_snapshot, daybook_data, module_profit_data)

REPORT_ROLES = ["ADMIN", "PHARMACIST", "ACCOUNTANT"]


@feature_required('reports')
def sales_report(request):
    rng = resolve_range(request)
    data = sales_report_data(rng['start'], rng['end'])
    if wants_csv(request):
        rows = [('Bills', data['bills']), ('Total billed', data['total']),
                ('Collected', data['paid']), ('On credit', data['credit']),
                ('Retail bills', data['retail']['bills']),
                ('Retail total', data['retail']['total']),
                ('Wholesale bills', data['wholesale']['bills']),
                ('Wholesale total', data['wholesale']['total'])]
        rows += [(f"Collected by {method}", v['total'])
                 for method, v in sorted(data['by_payment'].items())]
        return csv_response('sales-report', ['Measure', 'Value'], rows)
    return render(request, 'reports/sales_report.html', {'data': data, 'rng': rng})


@feature_required('profit')
def profit_report(request):
    rng = resolve_range(request)
    data = profit_report_data(rng['start'], rng['end'])
    if wants_csv(request):
        rows = [(it['name'], it['qty'], it['revenue'], it['profit'])
                for it in data['top_items']]
        return csv_response('profit-by-item',
                            ['Medicine', 'Qty sold', 'Revenue', 'Profit'], rows)
    return render(request, 'reports/profit_report.html', {'data': data, 'rng': rng})


@feature_required('daybook')
def daybook_report(request):
    rng = resolve_range(request)
    data = daybook_data(rng['start'], rng['end'])
    if wants_csv(request):
        rows = [(k.replace('_', ' ').title(), v) for k, v in data.items()
                if not isinstance(v, (dict, list))]
        return csv_response('day-book', ['Measure', 'Value'], rows)
    return render(request, 'reports/daybook.html', {'data': data, 'rng': rng})


@feature_required('profit')
def module_profit_report(request):
    """Revenue, cost and profit per module — "which part of the hospital earns".

    Only the modules the tenant actually bought produce rows (a module with no
    activity is dropped), so a pharmacy-only install sees one line rather than a
    column of zeroes for departments it does not have.
    """
    rng = resolve_range(request)
    rows, totals = module_profit_data(rng['start'], rng['end'])
    if wants_csv(request):
        out = [(r['label'], r['revenue'],
                r['cost'] if r['cost_tracked'] else 'not recorded',
                r['profit'] if r['cost_tracked'] else '') for r in rows]
        out.append(('TOTAL', totals['revenue'], totals['cost'], totals['profit']))
        return csv_response('profit-by-module',
                            ['Module', 'Revenue', 'Cost', 'Profit'], out)
    return render(request, 'reports/module_profit.html',
                  {'rows': rows, 'totals': totals, 'rng': rng})


@feature_required('reports')
def inventory_report(request):
    items, summary = inventory_snapshot()
    if wants_csv(request):
        rows = [(i['name'], i['generic_name'], i['brand'], i['category'],
                 i['quantity'], i['reorder_level'], i['price'],
                 i['wholesale_price'], i['expiry_date']) for i in items]
        return csv_response(
            'inventory',
            ['Medicine', 'Generic', 'Brand', 'Category', 'Qty', 'Reorder level',
             'Retail price', 'Wholesale price', 'Expiry'], rows)
    return render(request, 'reports/inventory_report.html', {'items': items, 'summary': summary})


@feature_required('reports')
def visual_analytics(request):
    import datetime
    import json
    from django.utils import timezone
    from django.db.models import Sum, Count, Q
    from decimal import Decimal
    from billing.models import Invoice, InvoiceItem
    from sales.models import Sale
    from opd.models import Appointment

    # Determine date range (current month)
    today = timezone.localdate()
    start_date = today.replace(day=1)
    
    if today.month == 12:
        end_date = today.replace(year=today.year + 1, month=1, day=1) - datetime.timedelta(days=1)
    else:
        end_date = today.replace(month=today.month + 1, day=1) - datetime.timedelta(days=1)

    hospital = request.user.hospital

    # 1. Department Revenue Breakdown
    # A. Pharmacy Sales
    # `is_returned=False`: a refunded sale keeps its `total`, so counting it here
    # left returned goods showing as revenue on the chart. The dashboard excluded
    # them all along; this screen did not.
    pharmacy_qs = Sale.objects.filter(created_at__date__range=(start_date, end_date),
                                      is_returned=False)
    # Fail-closed: key on superuser, not on "has a hospital". InvoiceItem and
    # Appointment below have no TenantManager, so a hospital-less non-superuser would
    # otherwise aggregate every tenant's revenue and doctor workload.
    if not request.user.is_superuser:
        pharmacy_qs = pharmacy_qs.filter(hospital=hospital)
    pharmacy_rev = pharmacy_qs.aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    # B. Service Invoices
    invoice_qs = InvoiceItem.objects.filter(
        invoice__status='ACTIVE',
        invoice__created_at__date__range=(start_date, end_date)
    )
    if not request.user.is_superuser:
        invoice_qs = invoice_qs.filter(invoice__hospital=hospital)

    # Classified with billing.revenue, the same rule the dashboard uses. These
    # were `icontains` tests, which is why a ward "Injection" charge counted as a
    # CT scan — 'Injection' contains 'ct'. Prefix matching, and one shared rule so
    # the two screens cannot drift apart again.
    from billing import revenue
    imaging_q = Q()
    for prefix in revenue.imaging_prefixes():
        imaging_q |= Q(description__istartswith=prefix)
    service_q = (Q(description__istartswith='OPD Consultation')
                 | Q(description__istartswith='Lab:') | imaging_q)

    def _sum(qs):
        return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    opd_rev = _sum(invoice_qs.filter(description__istartswith='OPD Consultation'))
    lab_rev = _sum(invoice_qs.filter(description__istartswith='Lab:'))
    imaging_rev = _sum(invoice_qs.filter(imaging_q))
    other_rev = _sum(invoice_qs.exclude(service_q))

    dept_labels = ["Pharmacy", "OPD Consultations", "Laboratory", "Imaging/Radiology"]
    dept_values = [float(pharmacy_rev), float(opd_rev), float(lab_rev), float(imaging_rev)]
    if other_rev > 0:
        dept_labels.append("Other Services")
        dept_values.append(float(other_rev))

    # 2. Doctor Patient Workload
    appt_qs = Appointment.objects.filter(
        status='DONE',
        appointment_date__range=(start_date, end_date)
    )
    if not request.user.is_superuser:
        appt_qs = appt_qs.filter(patient__hospital=hospital)
        
    doc_workload = appt_qs.values('doctor__full_name').annotate(count=Count('id')).order_by('-count')
    
    doc_labels = [item['doctor__full_name'] for item in doc_workload]
    doc_values = [item['count'] for item in doc_workload]

    # 3. Monthly Revenue Trend (Daily totals)
    trend_labels = []
    trend_values = []
    curr = start_date
    while curr <= today:
        day_ph = pharmacy_qs.filter(created_at__date=curr).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        day_inv = invoice_qs.filter(invoice__created_at__date=curr).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        day_total = day_ph + day_inv
        
        trend_labels.append(curr.strftime('%d %b'))
        trend_values.append(float(day_total))
        curr += datetime.timedelta(days=1)

    context = {
        'dept_labels_json': json.dumps(dept_labels),
        'dept_values_json': json.dumps(dept_values),
        'doc_labels_json': json.dumps(doc_labels),
        'doc_values_json': json.dumps(doc_values),
        'trend_labels_json': json.dumps(trend_labels),
        'trend_values_json': json.dumps(trend_values),
        'start_date': start_date,
        'end_date': end_date,
        'pharmacy_rev': pharmacy_rev,
        'opd_rev': opd_rev,
        'lab_rev': lab_rev,
        'imaging_rev': imaging_rev,
        'other_rev': other_rev,
        'total_rev': pharmacy_rev + opd_rev + lab_rev + imaging_rev + other_rev
    }
    return render(request, 'reports/visual_analytics.html', context)
