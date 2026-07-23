from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.decorators import role_required, feature_required
from reports.utils import resolve_range
from .availability import doctors_with_availability, split_by_availability
from .forms import (AppointmentForm, DepartmentForm, DoctorForm, DoctorPayoutForm,
                    DoctorScheduleFormSet, VisitForm)
from .models import (Appointment, Department, Doctor, DoctorAvailabilityOverride)
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
        formset = DoctorScheduleFormSet(request.POST)
        if form.is_valid():
            doctor = form.save()
            formset = DoctorScheduleFormSet(request.POST, instance=doctor)
            if formset.is_valid():
                formset.save()
                messages.success(request, 'Doctor profile created successfully.')
                return redirect('doctor_list')
    else:
        form = DoctorForm()
        formset = DoctorScheduleFormSet()
    return render(request, 'opd/doctor_form.html',
                  {'form': form, 'formset': formset, 'title': 'Add Doctor'})


@feature_required('doctors')
def doctor_edit(request, pk):
    """Edit a doctor — fees that auto-bill on each visit, and the OPD timings that
    decide whether reception is offered them."""
    doctor = get_object_or_404(Doctor, pk=pk)
    if request.method == 'POST':
        form = DoctorForm(request.POST, instance=doctor)
        formset = DoctorScheduleFormSet(request.POST, instance=doctor)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, f'{doctor.full_name} updated.')
            return redirect('doctor_list')
    else:
        form = DoctorForm(instance=doctor)
        formset = DoctorScheduleFormSet(instance=doctor)
    return render(request, 'opd/doctor_form.html',
                  {'form': form, 'formset': formset,
                   'title': f'Edit {doctor.full_name}', 'doctor': doctor})


# --- Departments ----------------------------------------------------------

@feature_required('doctors')
def department_list(request):
    """Departments and their doctors. Reception routes by department, so an empty
    one is worth seeing."""
    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            dept = form.save()
            messages.success(request, f"Department '{dept.name}' added.")
            return redirect('department_list')
    else:
        form = DepartmentForm()
    departments = Department.objects.prefetch_related('doctors').all()
    return render(request, 'opd/department_list.html',
                  {'departments': departments, 'form': form})


@feature_required('doctors')
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == 'POST':
        if department.doctors.exists():
            # SET_NULL would silently unfile every doctor in it.
            department.is_active = False
            department.save(update_fields=['is_active'])
            messages.info(request, f"'{department.name}' still has doctors — hidden instead of deleted.")
        else:
            name = department.name
            department.delete()
            messages.success(request, f"Department '{name}' deleted.")
    return redirect('department_list')


# --- Who is sitting right now ---------------------------------------------

@feature_required('appointments', 'opd')
def doctor_availability_board(request):
    """Today's OPD board: who is in, who is off, one click to change it."""
    doctors = doctors_with_availability(request.user.hospital if not request.user.is_superuser else None)
    sitting, away = split_by_availability(doctors)
    return render(request, 'opd/availability_board.html', {
        'sitting': sitting, 'away': away, 'today': timezone.localdate(),
    })


@feature_required('appointments', 'opd')
@require_POST
def doctor_availability_toggle(request, pk):
    """Mark a doctor off (or back on) for TODAY only.

    Written as a dated override rather than a flag on the doctor so today's leave
    cannot leak into tomorrow — the commonest way a manual switch goes wrong.
    """
    doctor = get_object_or_404(Doctor, pk=pk)
    today = timezone.localdate()
    wanted = request.POST.get('available') == '1'
    note = (request.POST.get('note') or '').strip()[:120]

    DoctorAvailabilityOverride.objects.filter(doctor=doctor, date=today).delete()

    if not wanted:
        DoctorAvailabilityOverride.objects.create(
            doctor=doctor, date=today, available=False, note=note, set_by=request.user)
        messages.success(request, f"{doctor.full_name} marked off for today.")
    elif doctor.availability()['available']:
        # Their normal timings already cover now — dropping the override is
        # enough, and leaves them following the schedule again tomorrow.
        messages.success(request, f"{doctor.full_name} is back on their normal timings.")
    else:
        # Sitting today even though the timings do not say so.
        DoctorAvailabilityOverride.objects.create(
            doctor=doctor, date=today, available=True, note=note, set_by=request.user)
        messages.success(request, f"{doctor.full_name} marked available for today.")

    return redirect(request.POST.get('next') or 'doctor_availability_board')


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


# --- Reception: register / find a patient, then book them in --------------

def _book_visit(request, patient, visit):
    """Create the appointment, notify the doctor and raise the consultation bill.

    One transaction: a token handed to the patient with no invoice behind it is
    money the desk never collects.
    """
    from accounts.models import Notification
    from billing.services import create_service_invoice

    doctor = visit['doctor']
    with transaction.atomic():
        appointment = Appointment.objects.create(
            patient=patient, doctor=doctor,
            appointment_date=visit['appointment_date'],
            slot_time=visit.get('slot_time'),
            visit_type=visit['visit_type'])
        fee = doctor.followup_fee if appointment.visit_type == 'FOLLOWUP' else doctor.opd_fee
        create_service_invoice(
            patient=patient,
            items=[(f"OPD Consultation — {doctor.full_name}", fee)],
            created_by=request.user, appointment=appointment)

    if doctor.user:
        Notification.objects.create(
            user=doctor.user,
            message=f"New Patient assigned: '{patient.full_name}' is in your queue (Token: {appointment.token_no}).",
            link=f"/patients/{patient.pk}/")
    return appointment


def _reception_context(request, **extra):
    hospital = request.user.hospital if not request.user.is_superuser else None
    doctors = doctors_with_availability(hospital)
    sitting, away = split_by_availability(doctors)
    ctx = {
        'departments': Department.objects.filter(is_active=True),
        'sitting': sitting,
        'away': away,
        'today': timezone.localdate(),
    }
    ctx.update(extra)
    return ctx


@feature_required('appointments')
def reception_desk(request):
    """The front desk's first screen: is this a new patient or an old one?

    Registering and then separately booking was two screens and a search in
    between; both paths now end on the same visit form.
    """
    q = (request.GET.get('q') or '').strip()
    results = None
    if q:
        from django.db.models import Value
        from django.db.models.functions import Replace
        from patients.models import Patient

        lookup = Q(mrn__icontains=q) | Q(phone__icontains=q) | Q(full_name__icontains=q)
        digits = ''.join(ch for ch in q if ch.isdigit())
        if digits:
            # CNICs are stored dashed (35202-1234567-1) and phones however they
            # were typed, so compare against a stripped copy — the desk reads the
            # number off a card and types it straight through.
            lookup |= Q(cnic_digits__contains=digits) | Q(phone_digits__contains=digits)
        results = (Patient.objects.filter(is_active=True)
                   .annotate(
                       cnic_digits=Replace(Replace('cnic', Value('-'), Value('')),
                                           Value(' '), Value('')),
                       phone_digits=Replace(Replace('phone', Value('-'), Value('')),
                                            Value(' '), Value('')))
                   .filter(lookup)
                   .order_by('full_name')[:20])
    return render(request, 'opd/reception_desk.html', {'q': q, 'results': results})


@feature_required('appointments')
def visit_create(request):
    """Book a visit. With `?patient=<pk>` the patient is already on file; without
    one, they are registered and booked in the same submit."""
    from patients.forms import PatientForm
    from patients.models import Patient

    patient_id = request.GET.get('patient') or request.POST.get('patient_id')
    patient = get_object_or_404(Patient, pk=patient_id) if patient_id else None
    is_new = patient is None

    if request.method == 'POST':
        visit_form = VisitForm(request.POST)
        patient_form = PatientForm(request.POST) if is_new else None
        forms_ok = visit_form.is_valid() and (patient_form.is_valid() if is_new else True)
        if forms_ok:
            if is_new:
                patient = patient_form.save()
            appointment = _book_visit(request, patient, visit_form.cleaned_data)
            messages.success(
                request,
                f"{patient.full_name} ({patient.mrn}) booked with "
                f"Dr. {appointment.doctor.full_name} — token {appointment.token_no}.")
            return redirect('appointment_slip', pk=appointment.pk)
    else:
        visit_form = VisitForm()
        patient_form = PatientForm() if is_new else None

    return render(request, 'opd/visit_form.html', _reception_context(
        request, visit_form=visit_form, patient_form=patient_form, patient=patient))


@feature_required('opd')
def appointment_slip(request, pk):
    """The token slip the patient carries to the doctor's room."""
    appointment = get_object_or_404(
        Appointment.objects.select_related('patient', 'doctor', 'doctor__department')
        .prefetch_related('doctor__schedules'),
        pk=pk)
    if not request.user.is_superuser:
        if appointment.patient.hospital_id != request.user.hospital_id:
            from django.http import Http404
            raise Http404
    doctor = appointment.doctor
    fee = doctor.followup_fee if appointment.visit_type == 'FOLLOWUP' else doctor.opd_fee
    return render(request, 'opd/appointment_slip.html',
                  {'appointment': appointment, 'fee': fee})


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


from django.http import JsonResponse

@feature_required('opd')
def appointment_update_status(request, pk):
    appointment = get_object_or_404(Appointment, pk=pk)
    status = request.GET.get('status')
    if status in dict(Appointment.STATUS_CHOICES):
        appointment.status = status
        appointment.save()
        return JsonResponse({'success': True, 'status': appointment.status})
    return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
