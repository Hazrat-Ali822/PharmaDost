from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import role_required, feature_required
from reports.utils import resolve_range
from .forms import AppointmentForm, DoctorForm, DoctorPayoutForm
from .models import Appointment, Doctor
from .services import doctor_earnings, payouts_total, payout_summary

PAYOUT_ROLES = ["ADMIN", "ACCOUNTANT"]


# --- Doctor roster: ADMIN only (staff management) -------------------------

@feature_required('doctors')
def doctor_list(request):
    doctors = Doctor.objects.filter(is_active=True)
    if request.user.hospital:
        doctors = doctors.filter(Q(user__hospital=request.user.hospital) | Q(user__isnull=True))
    return render(request, 'opd/doctor_list.html', {'doctors': doctors})


@feature_required('doctors')
def doctor_create(request):
    if request.method == 'POST':
        form = DoctorForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Doctor profile created successfully.')
            return redirect('doctor_list')
    else:
        form = DoctorForm()
    return render(request, 'opd/doctor_form.html', {'form': form, 'title': 'Add Doctor'})


@feature_required('doctors')
def doctor_edit(request, pk):
    """Edit a doctor — including the OPD / follow-up fees that auto-bill on each visit."""
    doctor = get_object_or_404(Doctor, pk=pk)
    if request.method == 'POST':
        form = DoctorForm(request.POST, instance=doctor)
        if form.is_valid():
            form.save()
            messages.success(request, f'{doctor.full_name} updated.')
            return redirect('doctor_list')
    else:
        form = DoctorForm(instance=doctor)
    return render(request, 'opd/doctor_form.html',
                  {'form': form, 'title': f'Edit {doctor.full_name}', 'doctor': doctor})


@feature_required('doctors')
@role_required(['ADMIN'])
def doctor_delete(request, pk):
    doctor = get_object_or_404(Doctor, pk=pk)
    
    # Check if doctor has any history
    has_history = False
    if doctor.appointments.exists():
        has_history = True
    elif hasattr(doctor, 'payouts') and doctor.payouts.exists():
        has_history = True
    elif hasattr(doctor, 'clinical_records') and doctor.clinical_records.exists():
        has_history = True
        
    if request.method == 'POST':
        action = request.POST.get('action', 'archive')
        if action == 'delete' and not has_history:
            name = doctor.full_name
            # Delete linked user account if exists
            if doctor.user:
                doctor.user.delete()
            doctor.delete()
            messages.success(request, f"Doctor '{name}' was permanently deleted.")
            return redirect('doctor_list')
        else:
            doctor.is_active = False
            doctor.save()
            messages.success(request, f"Doctor '{doctor.full_name}' was marked inactive.")
            return redirect('doctor_list')
            
    return render(request, 'opd/doctor_confirm_delete.html', {
        'doctor': doctor,
        'has_history': has_history
    })


# --- Appointments ---------------------------------------------------------

@feature_required('opd')
def appointment_list(request):
    appointments = Appointment.objects.select_related('patient', 'doctor').order_by('appointment_date', 'token_no')
    
    # Filter by hospital
    if request.user.hospital:
        appointments = appointments.filter(patient__hospital=request.user.hospital)
        
    role = getattr(request.user, 'role', None)
    is_doctor = role == 'DOCTOR' and not request.user.is_superuser
    is_unlinked_doctor = False
    
    if is_doctor:
        appointments = appointments.filter(doctor__user=request.user)
        if not Doctor.objects.filter(user=request.user).exists():
            is_unlinked_doctor = True
        
    show = request.GET.get('show', 'active')
    if show == 'active':
        appointments = appointments.exclude(status__in=['DONE', 'CANCELLED'])
    elif show == 'completed':
        appointments = appointments.filter(status='DONE')
        
    if request.GET.get('ajax') == '1':
        return render(request, 'opd/partials/appointment_list_rows.html', {
            'appointments': appointments,
            'show': show,
            'is_doctor': is_doctor
        })
        
    return render(request, 'opd/appointment_list.html', {
        'appointments': appointments,
        'show': show,
        'is_doctor': is_doctor,
        'is_unlinked_doctor': is_unlinked_doctor
    })


@feature_required('appointments')
def appointment_create(request):
    if request.method == 'POST':
        form = AppointmentForm(request.POST)
        if form.is_valid():
            appt = form.save()
            # Trigger notification for the doctor if they have a linked user account
            if appt.doctor.user:
                from accounts.models import Notification
                Notification.objects.create(
                    user=appt.doctor.user,
                    message=f"New Patient assigned: '{appt.patient.full_name}' is in your queue (Token: {appt.token_no}).",
                    link=f"/patients/{appt.patient.pk}/"
                )
            # auto-bill the consultation fee (pending) so it lands on the patient's bill
            from billing.services import create_service_invoice
            fee = appt.doctor.followup_fee if appt.visit_type == 'FOLLOWUP' else appt.doctor.opd_fee
            create_service_invoice(
                patient=appt.patient,
                items=[(f"OPD Consultation — {appt.doctor.full_name}", fee)],
                created_by=request.user, appointment=appt)
            messages.success(request, 'Appointment booked successfully.')
            return redirect('appointment_list')
    else:
        from django.utils import timezone
        now = timezone.localtime(timezone.now())
        form = AppointmentForm(initial={'slot_time': now.time().strftime('%H:%M')})
    return render(request, 'opd/appointment_form.html', {'form': form, 'title': 'Book Appointment'})


# --- Doctor payouts (finance): ADMIN / ACCOUNTANT -------------------------

@feature_required('payouts')
def payout_list(request):
    rng = resolve_range(request)
    rows = payout_summary(rng['start'], rng['end'])
    totals = {
        'consultations': sum(r['consultations'] for r in rows),
        'earned': sum((r['earned'] for r in rows), Decimal('0.00')),
        'paid': sum((r['paid'] for r in rows), Decimal('0.00')),
        'balance': sum((r['balance'] for r in rows), Decimal('0.00')),
    }
    return render(request, 'opd/payout_list.html', {'rows': rows, 'totals': totals, 'rng': rng})


@feature_required('payouts')
def payout_doctor(request, pk):
    doctor = get_object_or_404(Doctor, pk=pk)
    if request.method == 'POST':
        form = DoctorPayoutForm(request.POST)
        if form.is_valid():
            payout = form.save(commit=False)
            payout.doctor = doctor
            payout.paid_by = request.user
            payout.save()
            messages.success(request, f'Payout of Rs {payout.amount} recorded for {doctor.full_name}.')
            return redirect('payout_doctor', pk=doctor.pk)
    else:
        form = DoctorPayoutForm()

    all_earned = doctor_earnings(doctor)['share']
    all_paid = payouts_total(doctor)
    ctx = {
        'doctor': doctor,
        'form': form,
        'earned': all_earned,
        'paid': all_paid,
        'balance': all_earned - all_paid,
        'earnings': doctor_earnings(doctor),
        'payouts': doctor.payouts.select_related('paid_by').all(),
    }
    return render(request, 'opd/payout_doctor.html', ctx)
