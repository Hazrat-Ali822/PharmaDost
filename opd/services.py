"""Doctor payout accounting.

A doctor *earns* their share of the consultation fee for every consultation that
actually happened (appointment status Arrived / In-Consult / Done). The clinic
*pays out* that share over time via DoctorPayout rows. Balance = earned − paid.
"""
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from .models import Appointment, Doctor, DoctorPayout

# statuses that count as a consultation the doctor performed
SEEN_STATUSES = ('ARRIVED', 'IN_CONSULT', 'DONE')


def _fee_for(doctor, visit_type):
    return doctor.followup_fee if visit_type == 'FOLLOWUP' else doctor.opd_fee


def doctor_earnings(doctor, start=None, end=None):
    """Return {consultations, gross, share} for a doctor, optionally in a range.

    gross = total consultation fees; share = doctor's cut (gross * share_percent).
    Uses the doctor's *current* fees (per-appointment fees aren't stored).
    """
    qs = Appointment.objects.filter(doctor=doctor, status__in=SEEN_STATUSES)
    if start and end:
        qs = qs.filter(appointment_date__range=(start, end))

    consultations = 0
    gross = Decimal('0.00')
    for row in qs.values('visit_type').annotate(n=Count('id')):
        n = row['n']
        consultations += n
        gross += _fee_for(doctor, row['visit_type']) * n

    pct = doctor.share_percent if doctor.share_percent is not None else Decimal('100')
    share = (gross * Decimal(pct) / Decimal('100')).quantize(Decimal('0.01'))
    return {'consultations': consultations, 'gross': gross, 'share': share}


def payouts_total(doctor, start=None, end=None):
    qs = DoctorPayout.objects.filter(doctor=doctor)
    if start and end:
        qs = qs.filter(date__range=(start, end))
    return qs.aggregate(t=Coalesce(Sum('amount'), Decimal('0.00')))['t']


def payout_summary(start, end):
    """Per-doctor rows for the payout dashboard.

    Period figures are for the selected range; `balance` is ALL-TIME
    (total earned share − total paid) — i.e. what the doctor is still owed.
    """
    rows = []
    for d in Doctor.objects.all().order_by('full_name'):
        period = doctor_earnings(d, start, end)
        rows.append({
            'doctor': d,
            'consultations': period['consultations'],
            'gross': period['gross'],
            'earned': period['share'],
            'paid': payouts_total(d, start, end),
            'balance': doctor_earnings(d)['share'] - payouts_total(d),
        })
    return rows
