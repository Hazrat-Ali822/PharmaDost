"""Turn a report into a spreadsheet.

Every report in the app was screen-only. An accountant works in Excel — a page
they have to retype is a page they will retype wrong — so each report view now
answers `?export=csv` with a file.

**CSV, not .xlsx.** Excel opens it directly, it needs no new dependency on a
shared host, and it cannot carry a formula-injection payload the way a generated
workbook can (see `_safe` below). If a real workbook is ever wanted, add
openpyxl behind the same `csv_response` seam rather than changing the callers.
"""
import csv

from django.http import HttpResponse
from django.utils import timezone

# A cell starting with any of these is interpreted as a formula by Excel and
# LibreOffice — a patient named "=cmd|..." in an exported list becomes code on
# the accountant's machine. Prefixing with an apostrophe keeps the text visible
# and inert.
_DANGEROUS = ('=', '+', '-', '@', '\t', '\r')


def _safe(value):
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_DANGEROUS):
        return "'" + text
    return text


def csv_response(filename, header, rows):
    """`rows` is any iterable of sequences. Streams nothing — these reports are
    a screenful of aggregates, not a data dump, and a plain response keeps the
    tenant scoping the caller already applied."""
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}-{stamp}.csv"'
    # Excel on Windows needs the BOM to read UTF-8; without it a patient name in
    # Urdu or with an accent comes out as mojibake.
    resp.write('﻿')
    writer = csv.writer(resp)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(cell) for cell in row])
    return resp


def wants_csv(request):
    return request.GET.get('export') == 'csv'
