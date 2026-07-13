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
