from django.shortcuts import render
from accounts.decorators import role_required, feature_required
from .utils import (resolve_range, sales_report_data, profit_report_data,
                    inventory_snapshot, daybook_data)

REPORT_ROLES = ["ADMIN", "PHARMACIST", "ACCOUNTANT"]


@feature_required('reports')
def sales_report(request):
    rng = resolve_range(request)
    data = sales_report_data(rng['start'], rng['end'])
    return render(request, 'reports/sales_report.html', {'data': data, 'rng': rng})


@feature_required('profit')
def profit_report(request):
    rng = resolve_range(request)
    data = profit_report_data(rng['start'], rng['end'])
    return render(request, 'reports/profit_report.html', {'data': data, 'rng': rng})


@feature_required('daybook')
def daybook_report(request):
    rng = resolve_range(request)
    data = daybook_data(rng['start'], rng['end'])
    return render(request, 'reports/daybook.html', {'data': data, 'rng': rng})


@feature_required('reports')
def inventory_report(request):
    items, summary = inventory_snapshot()
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
    pharmacy_qs = Sale.objects.filter(created_at__date__range=(start_date, end_date))
    if hospital:
        pharmacy_qs = pharmacy_qs.filter(hospital=hospital)
    pharmacy_rev = pharmacy_qs.aggregate(total=Sum('total'))['total'] or Decimal('0.00')

    # B. Service Invoices
    invoice_qs = InvoiceItem.objects.filter(
        invoice__status='ACTIVE',
        invoice__created_at__date__range=(start_date, end_date)
    )
    if hospital:
        invoice_qs = invoice_qs.filter(invoice__hospital=hospital)

    opd_rev = invoice_qs.filter(description__icontains='OPD Consultation').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    lab_rev = invoice_qs.filter(description__icontains='Lab:').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    imaging_rev = invoice_qs.filter(
        Q(description__icontains='Ultrasound') |
        Q(description__icontains='X-Ray') |
        Q(description__icontains='CT') |
        Q(description__icontains='MRI') |
        Q(description__icontains='ECG') |
        Q(description__icontains='Echocardiography') |
        Q(description__icontains='Imaging')
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    other_rev = invoice_qs.exclude(
        Q(description__icontains='OPD Consultation') |
        Q(description__icontains='Lab:') |
        Q(description__icontains='Ultrasound') |
        Q(description__icontains='X-Ray') |
        Q(description__icontains='CT') |
        Q(description__icontains='MRI') |
        Q(description__icontains='ECG') |
        Q(description__icontains='Echocardiography') |
        Q(description__icontains='Imaging')
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

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
    if hospital:
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
