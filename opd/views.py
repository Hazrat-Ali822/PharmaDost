from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.decorators import role_required, feature_required
from user_mgmt.models import current_currency
from reports.utils import resolve_range
from .availability import doctors_with_availability, split_by_availability
from .forms import (AppointmentForm, DepartmentForm, DoctorForm, DoctorPayoutForm,
                    DoctorScheduleFormSet, VisitForm)
from .models import (Appointment, Department, Doctor, DoctorAvailabilityOverride)
from .scoping import scoped_doctors
from reports.export import csv_response, wants_csv
from .services import doctor_earnings, payouts_total, payout_summary

PAYOUT_ROLES = ["ADMIN", "ACCOUNTANT"]


# --- Doctor roster: ADMIN only (staff management) -------------------------

@feature_required('doctors')
def doctor_list(request):
    # Fail-closed via the shared helper: a hospital-less non-superuser must not
    # see every tenant's doctors.
    # Inactive doctors are LISTED, not hidden. Filtering them out here was the
    # only screen in the app that could edit a doctor's fee, share % and OPD
    # timings, so a doctor who had been deactivated — by anyone, at any time —
    # disappeared from it completely while still showing on the departments
    # page and in every booking dropdown. There was then no way back: nothing
    # in the product could reach the row to re-activate it. They are shown last
    # and badged instead.
    doctors = scoped_doctors(request.user, Doctor.objects.all()).order_by(
        '-is_active', 'full_name')
    return render(request, 'opd/doctor_list.html', {
        'doctors': doctors,
        'inactive_count': sum(0 if d.is_active else 1 for d in doctors),
    })


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
    doctors = doctors_with_availability(request.user)
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

@feature_required('appointments', 'opd')
def appointment_list(request):
    appointments = Appointment.objects.select_related('patient', 'doctor').order_by('appointment_date', 'token_no')

    # Fail closed: Appointment has no hospital column, so scope through the
    # patient. Keying on is_superuser (not `if user.hospital`) stops a
    # hospital-less non-superuser from reading every tenant's appointments.
    if not request.user.is_superuser:
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
        
    from pharma_mgmt.pagination import paginate
    page = paginate(request, appointments)

    if request.GET.get('ajax') == '1':
        return render(request, 'opd/partials/appointment_list_rows.html', {
            'appointments': page,
            'show': show,
            'is_doctor': is_doctor
        })

    return render(request, 'opd/appointment_list.html', {
        'appointments': page,
        'page_obj': page,
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
    from .services import bill_and_notify

    with transaction.atomic():
        appointment = Appointment.objects.create(
            patient=patient, doctor=visit['doctor'],
            appointment_date=visit['appointment_date'],
            slot_time=visit.get('slot_time'),
            visit_type=visit['visit_type'])
        bill_and_notify(appointment, request.user)
    return appointment


def _reception_context(request, **extra):
    doctors = doctors_with_availability(request.user)
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
        from patients.models import Patient
        from patients.search import apply_search

        # Shared with the patient registry — the desk and the registry must not
        # disagree about what counts as a match. See patients/search.py.
        results = apply_search(
            Patient.objects.filter(is_active=True), q).order_by('full_name')[:20]
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
        visit_form = VisitForm(request.POST, user=request.user)
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
        visit_form = VisitForm(user=request.user)
        patient_form = PatientForm() if is_new else None

    return render(request, 'opd/visit_form.html', _reception_context(
        request, visit_form=visit_form, patient_form=patient_form, patient=patient))


# Gated on BOTH keys: whoever can book a visit (`appointments`) must be able to
# reach the slip it produces, or a custom-access user with `appointments` but not
# `opd` books a patient — creating the token and invoice — then hits a 403 on the
# printable slip and can never open the queue either.
@feature_required('appointments', 'opd')
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
        form = AppointmentForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import bill_and_notify
            with transaction.atomic():
                appt = bill_and_notify(form.save(), request.user)
            messages.success(request, 'Appointment booked successfully.')
            return redirect('appointment_list')
    else:
        from django.utils import timezone
        now = timezone.localtime(timezone.now())
        form = AppointmentForm(initial={'slot_time': now.time().strftime('%H:%M')}, user=request.user)
    return render(request, 'opd/appointment_form.html', {'form': form, 'title': 'Book Appointment'})


# --- Doctor payouts (finance): ADMIN / ACCOUNTANT -------------------------

@feature_required('payouts')
def payout_list(request):
    rng = resolve_range(request)
    rows = payout_summary(rng['start'], rng['end'], scoped_doctors(request.user))
    totals = {
        'consultations': sum(r['consultations'] for r in rows),
        'earned': sum((r['earned'] for r in rows), Decimal('0.00')),
        'paid': sum((r['paid'] for r in rows), Decimal('0.00')),
        'balance': sum((r['balance'] for r in rows), Decimal('0.00')),
    }
    # The page has carried a "Download as CSV" button since exports were added,
    # but this view never answered `?export=csv` — so the link returned the HTML
    # page with a text/html content type and the browser simply re-rendered it.
    # Nothing downloaded, and nothing said why. The other six reports include
    # `reports/_range_filter.html`, which is where the button lives, so adding
    # the partial to a screen silently promises an export the view has to honour.
    if wants_csv(request):
        return csv_response(
            'doctor-payouts',
            ['Doctor', 'Consultations', 'Earned', 'Paid', 'Balance'],
            [[r['doctor'].display_name, r['consultations'], r['earned'],
              r['paid'], r['balance']] for r in rows])
    return render(request, 'opd/payout_list.html', {'rows': rows, 'totals': totals, 'rng': rng})


@feature_required('payouts')
def payout_doctor(request, pk):
    # Scoped, not `Doctor.objects`: this view also POSTs a DoctorPayout, so an
    # unscoped fetch let one tenant write money against another tenant's doctor.
    doctor = get_object_or_404(scoped_doctors(request.user), pk=pk)
    if request.method == 'POST':
        form = DoctorPayoutForm(request.POST)
        if form.is_valid():
            payout = form.save(commit=False)
            payout.doctor = doctor
            payout.paid_by = request.user
            payout.save()
            messages.success(request, f'Payout of {current_currency()} {payout.amount} recorded for {doctor.full_name}.')
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


def _get_tv_queue_data(user):
    today = timezone.localdate()
    doctors = doctors_with_availability(user)
    sitting, away = split_by_availability(doctors)

    # If some doctors are sitting now, show them; otherwise show all active doctors
    display_list = sitting if sitting else away

    queue = []
    for item in display_list:
        doc = item[0] if isinstance(item, (tuple, list)) else item
        state = item[1] if isinstance(item, (tuple, list)) else {}

        appts = Appointment.objects.filter(
            doctor=doc,
            appointment_date=today
        ).select_related('patient').order_by('token_no')

        # Serving: in consult, or first arrived
        in_consult = appts.filter(status='IN_CONSULT').first()
        if not in_consult:
            in_consult = appts.filter(status='ARRIVED').first()

        waiting_qs = appts.filter(status__in=['BOOKED', 'ARRIVED'])
        if in_consult:
            waiting_qs = waiting_qs.exclude(pk=in_consult.pk)

        waiting = list(waiting_qs[:6])
        done_count = appts.filter(status='DONE').count()
        total_count = appts.exclude(status='CANCELLED').count()

        queue.append({
            'doctor_id': doc.pk,
            'doctor_name': doc.full_name,
            'department': doc.department.name if doc.department else 'General OPD',
            'specialty': doc.specialty or '',
            'is_sitting': state.get('available', False) if isinstance(state, dict) else False,
            'state_label': state.get('label', '') if isinstance(state, dict) else '',
            'serving': {
                'token_no': in_consult.token_no,
                'patient_name': in_consult.patient.full_name,
                'status': in_consult.get_status_display(),
            } if in_consult else None,
            'waiting': [{
                'token_no': w.token_no,
                'patient_name': w.patient.full_name,
            } for w in waiting],
            'done_count': done_count,
            'total_count': total_count,
            'waiting_count': waiting_qs.count(),
        })
    return queue


@feature_required('tv_display', 'opd')
def opd_tv_display(request):
    """Full-screen Live OPD Waiting Hall TV Display for public TV / monitors."""
    queue = _get_tv_queue_data(request.user)
    return render(request, 'opd/tv_display.html', {
        'queue': queue,
        'today': timezone.localdate(),
    })


@feature_required('tv_display', 'opd')
def opd_tv_api(request):
    """JSON API endpoint for background auto-refreshing the TV display."""
    queue = _get_tv_queue_data(request.user)
    return JsonResponse({
        'success': True,
        'timestamp': timezone.now().isoformat(),
        'queue': queue,
    })

