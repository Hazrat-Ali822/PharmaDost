from datetime import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from django.db.models.deletion import ProtectedError

from accounts.decorators import feature_required, role_required
from accounts.models import User

from .forms import (EmployeeForm, LeaveRequestForm, SalaryPaymentForm,
                    StaffProfileForm)
from .models import Attendance, LeaveRequest, SalaryPayment, Shift, StaffProfile


def _staff(request):
    qs = User.objects.filter(is_active=True)
    if not request.user.is_superuser:
        qs = qs.filter(hospital=request.user.hospital)
    return qs.order_by('email')


def _parse_date(raw, default):
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date() if raw else default
    except (ValueError, TypeError):
        return default


@feature_required('hr')
def staff_list(request):
    staff = list(_staff(request))
    profiles = {p.user_id: p for p in StaffProfile.objects.all()}
    for u in staff:
        # NB: not `u.profile` — that name is the reverse OneToOne to
        # user_mgmt.UserProfile, and assigning a StaffProfile to it raises
        # ValueError (only surfaced once a StaffProfile row exists).
        u.hr_profile = profiles.get(u.id)
    total_payroll = sum((p.monthly_salary for p in profiles.values()), 0)
    return render(request, 'hr/staff_list.html', {'staff': staff, 'total_payroll': total_payroll})


@feature_required('hr')
@role_required(['ADMIN'])
def employee_add(request):
    """Put somebody on the payroll — with or without a login.

    Most of a clinic's staff never sign in. Before this the only route in was
    creating a user account, so a guard had to be given an email address before
    the attendance machine could count his days.
    """
    hospital = None if request.user.is_superuser else request.user.hospital
    if request.method == 'POST':
        form = EmployeeForm(request.POST, hospital=hospital)
        if form.is_valid():
            profile = form.save()
            _relink_after_new_staff(hospital)
            messages.success(
                request,
                f'{profile.user.get_full_name() or "Employee"} added.'
                + (' Their enrolment number is mapped, so punches from the '
                   'machine will count from now on.' if profile.biometric_id else
                   ' Add their enrolment number once their thumb is on the machine.'))
            return redirect('hr_staff_list')
    else:
        form = EmployeeForm(hospital=hospital)
    return render(request, 'hr/employee_form.html', {'form': form})


def _relink_after_new_staff(hospital):
    """A new mapping may match punches the machine already sent.

    Somebody is usually enrolled on the machine before anyone gets round to
    adding them here, so their first days arrive as unmapped punches. Attaching
    them at this point is what makes those days recoverable rather than lost.
    """
    try:
        from .views_biometric import _relink
        _relink(hospital)
    except Exception:                       # noqa: BLE001 — never block adding staff
        pass


@feature_required('hr')
def profile_edit(request, user_id):
    staff_user = get_object_or_404(_staff(request), pk=user_id)
    h = getattr(staff_user, 'hospital', None) or getattr(request.user, 'hospital', None)
    profile, created = StaffProfile.objects.get_or_create(user=staff_user, defaults={'hospital': h})
    if not created and profile.hospital is None and h is not None:
        profile.hospital = h
        profile.save(update_fields=['hospital'])
    if request.method == 'POST':
        form = StaffProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Staff profile saved.')
            return redirect('hr_staff_list')
    else:
        form = StaffProfileForm(instance=profile)
    return render(request, 'hr/profile_form.html', {'form': form, 'staff_user': staff_user, 'profile': profile})


@feature_required('hr')
def attendance_day(request):
    day = _parse_date(request.GET.get('date') or request.POST.get('date'), timezone.localdate())
    staff = list(_staff(request))
    if request.method == 'POST':
        existing = {a.user_id: a for a in Attendance.objects.filter(date=day)}
        for u in staff:
            status = request.POST.get(f'status_{u.id}')
            if not status:
                continue
            a = existing.get(u.id) or Attendance(user=u, date=day)
            a.status = status
            a.notes = request.POST.get(f'notes_{u.id}', '').strip()
            a.save()
        messages.success(request, f'Attendance saved for {day:%d/%m/%Y}.')
        return redirect(f"{reverse('hr_attendance')}?date={day:%Y-%m-%d}")
    marks = {a.user_id: a for a in Attendance.objects.filter(date=day)}
    for u in staff:
        u.att = marks.get(u.id)
    return render(request, 'hr/attendance.html', {
        'staff': staff, 'day': day, 'status_choices': Attendance.STATUS_CHOICES})


@feature_required('hr')
def attendance_summary(request):
    """Per-staff present/absent/leave counts for a month with deduction details."""
    from decimal import Decimal
    month = _parse_date((request.GET.get('month') or '') + '-01', timezone.localdate().replace(day=1))
    rows = (Attendance.objects.filter(date__year=month.year, date__month=month.month))
    staff = list(_staff(request))
    profiles = {p.user_id: p for p in StaffProfile.objects.all()}
    tally = {u.id: {'PRESENT': 0, 'ABSENT': 0, 'LEAVE': 0, 'HALF': 0} for u in staff}
    for a in rows:
        if a.user_id in tally:
            tally[a.user_id][a.status] = tally[a.user_id].get(a.status, 0) + 1
    for u in staff:
        u.tally = tally.get(u.id, {})
        u.hr_profile = profiles.get(u.id)
        allowed = u.hr_profile.allowed_monthly_leaves if u.hr_profile else 2
        leaves_taken = u.tally.get('LEAVE', 0)
        u.excess_leaves = max(0, leaves_taken - allowed)
        absents = u.tally.get('ABSENT', 0) + (u.tally.get('HALF', 0) * 0.5)
        u.total_deductible_days = absents + u.excess_leaves
        if u.hr_profile and u.hr_profile.enable_absence_deduction and u.total_deductible_days > 0:
            if u.hr_profile.deduction_per_absent_day and u.hr_profile.deduction_per_absent_day > 0:
                rate = u.hr_profile.deduction_per_absent_day
            else:
                rate = (u.hr_profile.monthly_salary or Decimal('0.00')) / Decimal('30.00')
            u.est_deduction = (Decimal(str(u.total_deductible_days)) * rate).quantize(Decimal('0.01'))
        else:
            u.est_deduction = Decimal('0.00')
    return render(request, 'hr/attendance_summary.html', {'staff': staff, 'month': month})


@feature_required('hr')
def leave_list(request):
    from pharma_mgmt.pagination import paginate
    leaves = LeaveRequest.objects.select_related('user').all()
    page = paginate(request, leaves)
    return render(request, 'hr/leave_list.html', {'leaves': page, 'page_obj': page})


@feature_required('hr')
def leave_create(request):
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Leave request added.')
            return redirect('hr_leave_list')
    else:
        form = LeaveRequestForm(user=request.user)
    return render(request, 'hr/leave_form.html', {'form': form})


@feature_required('hr')
def leave_decide(request, pk, decision):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    if request.method == 'POST' and decision in ('approve', 'reject'):
        leave.status = 'APPROVED' if decision == 'approve' else 'REJECTED'
        leave.decided_by = request.user
        leave.save(update_fields=['status', 'decided_by'])
        messages.success(request, f'Leave {leave.status.lower()}.')
    return redirect('hr_leave_list')


@feature_required('hr')
def salary_list(request):
    from pharma_mgmt.pagination import paginate
    payslips = SalaryPayment.objects.select_related('user').all()
    page = paginate(request, payslips)
    return render(request, 'hr/salary_list.html', {'payslips': page, 'page_obj': page})


@feature_required('hr')
def salary_create(request):
    from decimal import Decimal
    if request.method == 'POST':
        form = SalaryPaymentForm(request.POST, user=request.user)
        if form.is_valid():
            slip = form.save(commit=False)
            slip.paid_by = request.user
            slip.save()
            messages.success(request, 'Salary paid / payslip recorded.')
            return redirect('hr_salary_slip', pk=slip.pk)
    else:
        today = timezone.localdate()
        initial = {'paid_on': today,
                   'period': today.strftime('%B %Y')}
        uid = request.GET.get('user_id')
        if uid:
            prof = StaffProfile.objects.filter(user_id=uid).first()
            if prof:
                initial['user'] = prof.user_id
                initial['basic'] = prof.monthly_salary
                
                # Compute automated deductions based on month attendance & allowed leave quota
                month = today.replace(day=1)
                rows = Attendance.objects.filter(user_id=uid, date__year=month.year, date__month=month.month)
                absents = sum(1 for a in rows if a.status == 'ABSENT') + (sum(1 for a in rows if a.status == 'HALF') * 0.5)
                leaves = sum(1 for a in rows if a.status == 'LEAVE')
                allowed = prof.allowed_monthly_leaves
                excess_leaves = max(0, leaves - allowed)
                total_deductible = absents + excess_leaves
                
                if prof.enable_absence_deduction and total_deductible > 0:
                    if prof.deduction_per_absent_day and prof.deduction_per_absent_day > 0:
                        rate = prof.deduction_per_absent_day
                    else:
                        rate = (prof.monthly_salary or Decimal('0.00')) / Decimal('30.00')
                    deduction_amt = (Decimal(str(total_deductible)) * rate).quantize(Decimal('0.01'))
                    initial['deductions'] = deduction_amt
                    parts = []
                    if absents > 0:
                        parts.append(f"{absents:g} absent day(s)")
                    if excess_leaves > 0:
                        parts.append(f"{excess_leaves:g} excess leave(s)")
                    initial['note'] = f"Auto-deducted for {', '.join(parts)}."
                else:
                    initial['deductions'] = Decimal('0.00')
        form = SalaryPaymentForm(user=request.user, initial=initial)
    return render(request, 'hr/salary_form.html', {'form': form})


@feature_required('hr')
def salary_slip(request, pk):
    slip = get_object_or_404(SalaryPayment.objects.select_related('user'), pk=pk)
    return render(request, 'hr/salary_slip.html', {'slip': slip})


# ---------------------------------------------------------------- shifts
# Working shifts are per-hospital (`hr.Shift`) rather than a fixed
# morning/evening/night list, because no two hospitals run the same day. The
# editor is reachable from HR *and* from the ward roster, so it is gated on any
# of those features rather than `hr` alone — a hospital running IPD without the
# HR module still has to be able to name its own shifts.

@role_required(['ADMIN'])
@feature_required('hr', 'ipd', 'ward_manage')
def shift_list(request):
    hospital = None if request.user.is_superuser else request.user.hospital
    if request.method == 'POST':
        return _shift_save(request, hospital)
    return render(request, 'hr/shift_list.html', {
        'shifts': Shift.for_hospital(hospital, include_inactive=True),
    })


def _shift_save(request, hospital):
    """Add one shift, or save edits to the existing rows in one submit."""
    if request.POST.get('action') == 'add':
        name = (request.POST.get('name') or '').strip()
        start, end = request.POST.get('start_time'), request.POST.get('end_time')
        if not (name and start and end):
            messages.error(request, 'A shift needs a name, a start and an end.')
        elif Shift.all_objects.filter(hospital=hospital, name__iexact=name).exists():
            messages.error(request, f'There is already a shift called "{name}".')
        else:
            last = Shift.all_objects.filter(hospital=hospital).count()
            Shift.all_objects.create(hospital=hospital, name=name, start_time=start,
                                     end_time=end, order=last)
            messages.success(request, f'Shift "{name}" added.')
        return redirect('hr_shift_list')

    # Bulk save. Scoped through `all_objects` on the hospital *value*, not
    # `objects`: TenantManager lets a superuser past unfiltered, and this writes.
    for s in Shift.all_objects.filter(hospital=hospital):
        name = (request.POST.get(f'name_{s.pk}') or '').strip()
        start = request.POST.get(f'start_{s.pk}')
        end = request.POST.get(f'end_{s.pk}')
        if not (name and start and end):
            continue
        s.name, s.start_time, s.end_time = name, start, end
        s.order = _int(request.POST.get(f'order_{s.pk}'), s.order)
        s.is_active = request.POST.get(f'active_{s.pk}') == 'on'
        s.save()
    messages.success(request, 'Shifts saved.')
    return redirect('hr_shift_list')


def _int(raw, fallback):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


@role_required(['ADMIN'])
@feature_required('hr', 'ipd', 'ward_manage')
def shift_delete(request, pk):
    """Delete a shift nobody has used; otherwise say so and leave it alone.

    Rosters, allocations, notes and handovers all PROTECT their shift, so a used
    one cannot be removed without taking history with it. Deactivating keeps old
    records readable and takes it out of every dropdown, which is what "we don't
    run that shift any more" actually means.
    """
    hospital = None if request.user.is_superuser else request.user.hospital
    shift = get_object_or_404(Shift.all_objects, pk=pk, hospital=hospital)
    if request.method == 'POST':
        try:
            shift.delete()
            messages.success(request, f'Shift "{shift.name}" deleted.')
        except ProtectedError:
            shift.is_active = False
            shift.save(update_fields=['is_active'])
            messages.info(request, f'"{shift.name}" is used on the roster, so it has '
                                   f'been switched off instead of deleted — old records '
                                   f'still read correctly.')
    return redirect('hr_shift_list')
