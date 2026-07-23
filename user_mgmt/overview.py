"""What the owner needs to see without being told: today's numbers, what needs
attention, and who did what.

The admin dashboard was a menu of links — it told you where to go, never what
had happened. This is the other half. Everything here is read-only and scoped to
the current tenant by the models' own managers.

Query cost matters: this runs on every admin page load of `/`. Each section is a
handful of aggregates, and anything that would grow with the number of rows on
screen is either capped or prefetched.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone


def _money(value):
    return value or Decimal('0.00')


def todays_numbers(user):
    """Counters for the day. Each is one aggregate — no per-row work."""
    from billing.models import Invoice
    from ipd.models import Admission
    from opd.models import Appointment
    from patients.models import Patient
    from sales.models import Sale

    today = timezone.localdate()
    day_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time()))

    sales = Sale.objects.filter(created_at__gte=day_start).aggregate(
        n=Count('id'), total=Sum('total'))
    invoices = Invoice.objects.filter(created_at__gte=day_start).aggregate(
        n=Count('id'), total=Sum('total'), paid=Sum('paid'))

    return {
        'patients_registered': Patient.objects.filter(created_at__gte=day_start).count(),
        'appointments': Appointment.objects.filter(appointment_date=today).count(),
        'admissions': Admission.objects.filter(admission_date__gte=day_start).count(),
        'discharges': Admission.objects.filter(discharge_date__gte=day_start).count(),
        'sales_count': sales['n'] or 0,
        'sales_total': _money(sales['total']),
        'invoices_count': invoices['n'] or 0,
        'invoices_total': _money(invoices['total']),
        'invoices_paid': _money(invoices['paid']),
        'invoices_due': _money(invoices['total']) - _money(invoices['paid']),
    }


def attention_items(user):
    """Things that are wrong or waiting, worst first.

    Only counts — the admin clicks through for detail. A count that is zero is
    dropped rather than shown as a reassuring '0', so the list is short enough to
    actually be read.
    """
    from billing.models import Invoice
    from inventory.models import Medicine
    from ipd.models import AdmissionRequest
    from lab.models import TestOrder
    from ot.models import SurgeryRequest
    from prescriptions.models import Prescription

    today = timezone.localdate()
    items = []

    def add(count, label, link, tone='warn'):
        if count:
            items.append({'count': count, 'label': label, 'link': link, 'tone': tone})

    # money owed. `Invoice.objects` already excludes voided ones; "unpaid" is
    # paid < total, since there is no PAID status to filter on.
    add(Invoice.objects.filter(paid__lt=F('total')).count(),
        'unpaid invoice(s)', '/billing/invoices/', 'warn')

    # stock — the two cron alerts, visible without waiting for the daily run
    medicines = list(Medicine.objects.filter(is_active=True).prefetch_related('batches'))
    add(sum(1 for m in medicines if m.is_low_stock), 'medicine(s) at or below reorder level',
        '/medicines/reorder/', 'warn')
    add(sum(1 for m in medicines if m.expired_quantity), 'medicine(s) with expired stock',
        '/medicines/expiry/', 'bad')

    # queues waiting on somebody
    add(AdmissionRequest.objects.filter(status='Pending').count(),
        'admission request(s) awaiting a bed', '/ipd/requests/')
    add(SurgeryRequest.objects.filter(status='Pending').count(),
        'surgery request(s) to schedule', '/ot/requests/')
    add(Prescription.objects.filter(status__in=['PENDING', 'PARTIAL']).count(),
        'prescription(s) not yet dispensed', '/prescriptions/')
    add(TestOrder.objects.filter(status='Pending').count(),
        'lab order(s) without results', '/lab/orders/')

    # security — failed logins are the one thing nobody else will report
    from audit.models import AuditLog
    since = timezone.now() - timedelta(days=1)
    add(AuditLog.objects.filter(action='LOGIN_FAILED', timestamp__gte=since).count(),
        'failed login attempt(s) in the last 24h', '/audit/?action=LOGIN_FAILED', 'bad')

    return items


def opd_board(user):
    """Which doctors are actually sitting — the question reception is asked all day."""
    from opd.availability import doctors_with_availability, split_by_availability

    hospital = user.hospital if not user.is_superuser else None
    sitting, away = split_by_availability(doctors_with_availability(hospital))
    return {'sitting': sitting, 'away': away}


def recent_activity(user, limit=12):
    """Who did what, most recent first. Tenant-scoped by AuditLog's manager."""
    from audit.models import AuditLog
    return AuditLog.objects.select_related('user')[:limit]


def build(user):
    return {
        'today': todays_numbers(user),
        'attention': attention_items(user),
        'board': opd_board(user),
        'activity': recent_activity(user),
        'as_of': timezone.localtime(),
    }
