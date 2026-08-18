from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from accounts.decorators import feature_required, role_required
from user_mgmt.models import current_currency
from accounts.models import Notification
from inventory.models import Medicine
from inventory.safety import screen_medicines
from patients.models import Patient
from .models import Ward, Bed, Admission, DoctorRound, MedicationLog, AdmissionRequest
from .forms import (WardForm, BedForm, AdmissionForm, DoctorRoundForm, DischargeForm,
                    MedicationLogForm, VitalsObservationForm, FluidBalanceEntryForm,
                    NursingNoteForm, ShiftHandoverForm, CareTaskForm)

def _scoped_admissions(request):
    """Admissions this user is allowed to see.

    `Admission` carries a hospital FK, so `TenantManager` already keeps tenants
    apart. This adds the *clinical* narrowing on top: a doctor sees only their
    own inpatients — the ones they are attending, plus the ones they advised for
    admission (reception may allot a different attending doctor, but the doctor
    who asked for the bed still owns that patient). Mirrors `_scoped_orders` in
    lab and `_scoped_studies` in imaging, so a doctor cannot reach a colleague's
    ward chart by guessing an admission id.

    Everyone else with `ipd`/`ward` (admin, reception, nurse) needs the whole
    ward to do their job, so they are not narrowed.
    """
    qs = Admission.objects.all()
    if getattr(request.user, 'role', None) == 'DOCTOR' and not request.user.is_superuser:
        qs = qs.filter(
            Q(attending_doctor__user=request.user)
            | Q(from_request__advised_by=request.user)
        ).distinct()
    return qs


@feature_required('ipd', 'ward')
def admission_list(request):
    scoped = _scoped_admissions(request)
    active_admissions = scoped.filter(status='Admitted').select_related('patient', 'bed__ward', 'attending_doctor')
    past_admissions = scoped.filter(status='Discharged').select_related('patient', 'bed__ward', 'attending_doctor').order_by('-discharge_date')[:50]
    return render(request, 'ipd/admission_list.html', {
        'active_admissions': active_admissions,
        'past_admissions': past_admissions,
    })

@feature_required('ipd')
def admission_create(request):
    # optional: confirming a doctor's admission advice (from the reception queue)
    req_id = request.GET.get('request_id') or request.POST.get('request_id')
    adm_req = AdmissionRequest.objects.filter(pk=req_id, status='Pending').first() if req_id else None

    if request.method == 'POST':
        form = AdmissionForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import admit_patient
            admission = form.save(commit=False)
            try:
                res = admit_patient(admission, request.user, adm_request=adm_req)
                messages.success(request, f"Patient {admission.patient.full_name} admitted successfully to Bed {res.bed.bed_number}.")
                return redirect('ipd:admission_detail', pk=admission.pk)
            except ValidationError as e:
                messages.error(request, e.messages[0] if getattr(e, 'messages', None) else str(e))
    else:
        initial = {}
        bed_id = request.GET.get('bed_id')
        if bed_id:
            try:
                bed = Bed.objects.get(pk=bed_id, status='Available')
                initial['bed'] = bed
            except Bed.DoesNotExist:
                pass
        if adm_req:
            initial['patient'] = adm_req.patient
            initial['admission_reason'] = adm_req.reason
        form = AdmissionForm(initial=initial, user=request.user)
    return render(request, 'ipd/admission_form.html', {
        'form': form,
        'title': 'Admit New Patient',
        'request_id': req_id or '',
        'adm_req': adm_req,
    })

@feature_required('ipd', 'ward')
def admission_detail(request, pk):
    admission = get_object_or_404(
        _scoped_admissions(request).select_related('patient', 'bed__ward', 'attending_doctor'), pk=pk)
    rounds = admission.rounds.all().order_by('-round_time')
    medication_logs = (admission.medication_logs.all()
                       .select_related('administered_by', 'medicine')
                       .order_by('-administered_at'))

    # The ward is where drugs are physically given, so it needs the clinical
    # picture the doctor already has: what was prescribed, what was ordered, and
    # what came back. Scoped to THIS admission's patient, who is already
    # tenant-checked by fetching the admission above.
    patient = admission.patient
    from prescriptions.models import Prescription
    from lab.models import TestOrder
    from imaging.models import ImagingStudy

    prescriptions = (Prescription.objects
                     .filter(appointment__patient=patient)
                     .select_related('appointment__doctor')
                     .prefetch_related('items__medicine')
                     .order_by('-created_at')[:5])
    lab_orders = (TestOrder.objects.filter(patient=patient)
                  .prefetch_related('results__lab_test')
                  .order_by('-order_date')[:5])
    imaging_studies = (ImagingStudy.objects.filter(patient=patient)
                       .order_by('-study_date')[:5])

    medicine_total = sum((log.charge for log in medication_logs), Decimal('0.00'))

    observations = list(admission.observations.select_related('taken_by')[:12])
    latest_obs = observations[0] if observations else None
    fluid_today = fluid_totals(admission, timezone.localdate())
    fluid_entries = admission.fluid_entries.select_related('recorded_by')[:12]
    nursing_notes = admission.nursing_notes.select_related('noted_by')[:10]
    care_tasks = admission.care_tasks.select_related('done_by')[:12]

    return render(request, 'ipd/admission_detail.html', {
        'admission': admission,
        'rounds': rounds,
        'medication_logs': medication_logs,
        'medicine_total': medicine_total,
        'prescriptions': prescriptions,
        'lab_orders': lab_orders,
        'imaging_studies': imaging_studies,
        'observations': observations,
        'latest_obs': latest_obs,
        'fluid_today': fluid_today,
        'fluid_entries': fluid_entries,
        'nursing_notes': nursing_notes,
        'care_tasks': care_tasks,
    })

@feature_required('ward')
def medication_log_add(request, pk):
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = MedicationLogForm(request.POST)
        if form.is_valid():
            from .services import log_medication
            res = log_medication(admission, form.save(commit=False),
                                 form.cleaned_data.get('medicine'), request.user)
            log, stock_short = res.log, res.stock_short

            for warning in res.warnings:
                messages.warning(request, warning)

            if stock_short:
                messages.warning(
                    request,
                    f"{log.medicine_name} x{log.quantity} recorded on the chart. "
                    f"{stock_short} — nothing was deducted from stock or added to the "
                    f"bill. Ask the pharmacy to reconcile."
                )
            elif log.charge:
                messages.success(
                    request,
                    f"{log.medicine_name} x{log.quantity} recorded — stock reduced, "
                    f"{current_currency()} {log.charge} added to the discharge bill."
                )
            elif log.source != 'PHARMACY':
                messages.success(
                    request,
                    f"{log.medicine_name} x{log.quantity} recorded from the patient's own "
                    f"supply — no stock movement, nothing billed."
                )
            else:
                messages.success(request, f"Medication '{log.medicine_name}' logged successfully.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = MedicationLogForm()
    return render(request, 'ipd/medication_form.html', _medication_ctx(form, admission))


def _medication_ctx(form, admission):
    return {
        'form': form,
        'admission': admission,
        'prescribed_medicines': _prescribed_medicines(admission.patient),
        'pharmacy_medicines': _pharmacy_medicines(),
    }


def _prescribed_medicines(patient):
    """What the doctor actually ordered for this patient, newest first.

    This — not the whole catalogue — is the ward's working list: a nurse gives
    what was prescribed, and making them find it among every drug in the building
    is how the wrong row gets picked at 3am. Off-catalogue items the doctor wrote
    by hand are included too, with no id, so they record without stock or charge.
    """
    from prescriptions.models import PrescriptionItem

    items = (PrescriptionItem.objects
             .filter(prescription__appointment__patient=patient, is_cancelled=False)
             .select_related('medicine', 'prescription')
             .prefetch_related('medicine__batches')
             .order_by('-prescription__created_at'))

    seen, out = set(), []
    for item in items:
        med = item.medicine
        label = (f"{med.name} ({med.brand})" if med and med.brand
                 else med.name if med else item.custom_medicine_name)
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'label': label,
            'medicine_id': med.id if med else '',
            'price': med.price if med else '',
            # advisory only — a short stock level no longer blocks the nurse
            'stock': med.sellable_quantity if med else '',
            'dosage': item.dosage,
            'instructions': item.instructions,
            'prescribed_on': item.prescription.created_at,
        })
    return out


def _pharmacy_medicines():
    """Full catalogue, behind a toggle on the ward medication form, for when the
    doctor's order was written on paper or during a round.

    Tenant-scoped by `Medicine`'s manager. `batches` is prefetched because the
    template shows sellable stock per row, which reads them (see CLAUDE.md).
    """
    return (Medicine.objects.filter(is_active=True)
            .prefetch_related('batches')
            .order_by('name', 'brand'))

@feature_required('ward')
def doctor_round_add(request, pk):
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = DoctorRoundForm(request.POST)
        if form.is_valid():
            round_log = form.save(commit=False)
            round_log.admission = admission
            round_log.save()
            messages.success(request, "Doctor round checklist recorded successfully.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = DoctorRoundForm()
    return render(request, 'ipd/round_form.html', {
        'form': form,
        'admission': admission,
    })

@feature_required('ipd')
def admission_discharge(request, pk):
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if admission.status == 'Discharged':
        messages.error(request, "Patient is already discharged.")
        return redirect('ipd:admission_detail', pk=admission.pk)
        
    if request.method == 'POST':
        form = DischargeForm(request.POST, instance=admission)
        if form.is_valid():
            from .services import discharge_patient
            res = discharge_patient(form.save(commit=False), request.user)
            adm, med_total, est_bed_charges = res.admission, res.med_total, res.bed_charges

            if med_total:
                messages.success(
                    request,
                    f"Patient {adm.patient.full_name} discharged. Invoice generated: "
                    f"bed charges {current_currency()} {est_bed_charges} + medicines {current_currency()} {med_total}."
                )
            else:
                messages.success(request, f"Patient {adm.patient.full_name} has been discharged. Bed charges invoice generated.")
            # Land on the printable discharge summary — the patient leaves with it.
            return redirect('ipd:discharge_summary', pk=adm.pk)
    else:
        form = DischargeForm(instance=admission)

    # Estimated days stayed (inclusive of admission + today), for the confirm screen
    days = (timezone.localdate() - admission.admission_date.date()).days + 1
    if days < 1:
        days = 1
    est_bed_charges = days * admission.bed.ward.daily_rate
    
    return render(request, 'ipd/discharge_form.html', {
        'form': form,
        'admission': admission,
        'days': days,
        'est_bed_charges': est_bed_charges,
    })

@feature_required('ipd', 'ward')
def discharge_summary(request, pk):
    """The printable A4 discharge sheet: stay, diagnosis, medications given,
    and the itemised bill. Reachable after discharge and from the admission page."""
    admission = get_object_or_404(
        _scoped_admissions(request)
        .select_related('patient', 'bed__ward', 'attending_doctor', 'discharge_invoice'),
        pk=pk)
    logs = (admission.medication_logs.select_related('medicine')
            .order_by('administered_at'))
    rounds = admission.rounds.order_by('round_time')
    invoice = admission.discharge_invoice
    return render(request, 'ipd/discharge_summary.html', {
        'admission': admission,
        'logs': logs,
        'rounds': rounds,
        'invoice': invoice,
        'items': invoice.items.all() if invoice else [],
    })


@feature_required('ipd', 'ward')
def ward_bed_list(request):
    wards = Ward.objects.prefetch_related('beds').all()
    return render(request, 'ipd/ward_bed_list.html', {
        'wards': wards,
    })

@feature_required('ipd')
def ward_create(request):
    if request.method == 'POST':
        form = WardForm(request.POST)
        if form.is_valid():
            ward = form.save()
            messages.success(request, f"Ward '{ward.name}' created.")
            return redirect('ipd:ward_bed_list')
    else:
        form = WardForm()
    return render(request, 'ipd/ward_form.html', {
        'form': form,
        'title': 'Create Ward'
    })

@feature_required('ipd')
def bed_create(request):
    if request.method == 'POST':
        form = BedForm(request.POST)
        if form.is_valid():
            bed = form.save()
            messages.success(request, f"Bed '{bed.bed_number}' registered.")
            return redirect('ipd:ward_bed_list')
    else:
        form = BedForm()
    return render(request, 'ipd/bed_form.html', {
        'form': form,
        'title': 'Add Bed'
    })

@feature_required('ipd')
def bed_edit(request, pk):
    bed = get_object_or_404(Bed, pk=pk)
    if request.method == 'POST':
        form = BedForm(request.POST, instance=bed)
        if form.is_valid():
            bed = form.save()
            messages.success(request, f"Bed '{bed.bed_number}' status updated.")
            return redirect('ipd:ward_bed_list')
    else:
        form = BedForm(instance=bed)
    return render(request, 'ipd/bed_form.html', {
        'form': form,
        'title': 'Edit Bed Details',
        'bed': bed,
    })

@feature_required('ipd')
def bed_delete(request, pk):
    bed = get_object_or_404(Bed, pk=pk)
    if request.method == 'POST':
        bed_number = bed.bed_number
        bed.delete()
        messages.success(request, f"Bed '{bed_number}' has been deleted.")
        return redirect('ipd:ward_bed_list')
    return render(request, 'ipd/bed_confirm_delete.html', {'bed': bed})


# ---------------------------------------------------------------------------
# Admission advice (doctor -> reception/ward handoff)
# ---------------------------------------------------------------------------

@feature_required('patients')
@role_required(['ADMIN', 'DOCTOR'])
def admission_advise(request, patient_id):
    """A doctor advises that this patient be admitted. Creates a pending request and
    notifies the reception / ward desk, who then allot a bed and confirm."""
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        ward_id = request.POST.get('preferred_ward') or None
        if not reason:
            messages.error(request, 'Please enter a reason for admission.')
        else:
            AdmissionRequest.objects.create(
                patient=patient, advised_by=request.user, reason=reason,
                preferred_ward_id=ward_id or None)
            Notification.send_to_role(
                hospital=patient.hospital, role='RECEPTIONIST',
                message=f"🛏️ Admission advised: {patient.full_name} — please allot a bed.",
                link='/ipd/requests/')
            Notification.send_to_role(
                hospital=patient.hospital, role='ADMIN',
                message=f"🛏️ Admission advised for {patient.full_name}.",
                link='/ipd/requests/')
            messages.success(request, f"Admission advised for {patient.full_name}. Reception has been notified.")
            return redirect('patient_detail', pk=patient.pk)
    return render(request, 'ipd/admission_advise.html', {
        'patient': patient,
        'wards': Ward.objects.all().order_by('name'),
    })


@feature_required('ipd')
def admission_request_list(request):
    """Reception / ward queue of pending admission advices to act on.

    Reception and admin act on the whole queue; a doctor only follows up the
    advices they raised themselves."""
    qs = AdmissionRequest.objects.all()
    if getattr(request.user, 'role', None) == 'DOCTOR' and not request.user.is_superuser:
        qs = qs.filter(advised_by=request.user)
    pending = (qs.filter(status='Pending')
               .select_related('patient', 'advised_by', 'preferred_ward')
               .order_by('created_at'))
    recent = (qs.exclude(status='Pending')
              .select_related('patient', 'admission')
              .order_by('-created_at')[:20])
    return render(request, 'ipd/admission_request_list.html', {'pending': pending, 'recent': recent})


@feature_required('ipd')
def admission_request_cancel(request, pk):
    req = get_object_or_404(AdmissionRequest, pk=pk)
    if request.method == 'POST':
        req.status = 'Cancelled'
        req.save(update_fields=['status'])
        messages.info(request, 'Admission request cancelled.')
    return redirect('ipd:admission_request_list')


# --------------------------------------------------------------------------
# Nursing / Ward management — duty roster, patient allocation, my duties
#
# Two capabilities: `ward` (every nurse) can VIEW the roster/board and their own
# duties; `ward_manage` (Ward In-charge / Admin) BUILDS the roster and allocates
# patients. Mirrors how the ward feature is narrower than ipd.
# --------------------------------------------------------------------------
from datetime import date, timedelta                                    # noqa: E402
from django.urls import reverse                                         # noqa: E402
from accounts.models import User                                        # noqa: E402
from hr.models import Shift                                            # noqa: E402
from .models import (OBS_INTERVAL_HOURS, NurseShift,                    # noqa: E402
                     PatientAllocation, VitalsObservation, FluidBalanceEntry, fluid_totals,
                     NursingNote, ShiftHandover, CareTask, ward_census)


def _shifts(request):
    """This hospital's own shifts. Never a hardcoded list — see `hr.Shift`."""
    hospital = None if request.user.is_superuser else request.user.hospital
    return list(Shift.for_hospital(hospital))


def _hospital_nurses(request):
    """Active nurses of this hospital (admins also take ward duty). Fail-closed:
    a hospital-less non-superuser matches only hospital-less users."""
    qs = User.objects.filter(is_active=True, role__in=['NURSE', 'ADMIN'])
    if not request.user.is_superuser:
        qs = qs.filter(hospital=request.user.hospital)
    return qs.order_by('first_name', 'last_name', 'email')


def _current_shift(request):
    """The hospital's own shift that is running now (a `Shift`, or None).

    Reads the configured times rather than assuming 07/14/21, and so handles a
    night shift crossing midnight — see `Shift.covers`.
    """
    hospital = None if request.user.is_superuser else request.user.hospital
    return Shift.current(hospital)


def _pick_shift(request, shifts, param='shift'):
    """The shift named in the querystring, else the one running now."""
    raw = request.GET.get(param)
    for s in shifts:
        if str(s.pk) == raw:
            return s
    return _current_shift(request) or (shifts[0] if shifts else None)


def _parse_date(value, fallback):
    try:
        return date.fromisoformat(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


@feature_required('ward')
def duty_roster(request):
    """The weekly duty roster for a ward — who is on which shift. In-charge/Admin
    edits; every nurse can look."""
    wards = list(Ward.objects.all().order_by('name'))
    ward = next((w for w in wards if str(w.pk) == request.GET.get('ward')), wards[0] if wards else None)
    anchor = _parse_date(request.GET.get('week'), timezone.localdate())
    start = anchor - timedelta(days=anchor.weekday())        # Monday
    days = [start + timedelta(days=i) for i in range(7)]

    grid = {}
    if ward:
        for e in (NurseShift.objects.filter(ward=ward, date__range=(days[0], days[-1]))
                  .select_related('nurse')):
            grid.setdefault((e.date.isoformat(), e.shift_id), []).append(e)

    shifts = _shifts(request)
    rows = [{
        'shift': sh,
        'label': sh.name,
        'time': sh.time_range,
        'cells': [{'date': d, 'shift': sh, 'entries': grid.get((d.isoformat(), sh.pk), [])}
                  for d in days],
    } for sh in shifts]

    can_manage = request.user.is_superuser or request.user.has_feature('ward_manage')
    return render(request, 'ipd/duty_roster.html', {
        'wards': wards, 'ward': ward, 'days': days, 'rows': rows,
        'nurses': _hospital_nurses(request), 'shifts': shifts,
        'week_start': start, 'prev_week': start - timedelta(days=7),
        'next_week': start + timedelta(days=7), 'today': timezone.localdate(),
        'can_manage': can_manage,
    })


@feature_required('ward_manage')
def roster_add(request):
    ward_id = request.POST.get('ward')
    week = request.POST.get('date', '')
    if request.method == 'POST':
        nurse = _hospital_nurses(request).filter(pk=request.POST.get('nurse')).first()
        ward = Ward.objects.filter(pk=ward_id).first()
        shift = next((s for s in _shifts(request)
                      if str(s.pk) == request.POST.get('shift')), None)
        d = _parse_date(request.POST.get('date'), None)
        duty = request.POST.get('duty') if request.POST.get('duty') in ('STAFF', 'INCHARGE') else 'STAFF'
        if nurse and ward and shift and d:
            obj, created = NurseShift.objects.get_or_create(
                nurse=nurse, date=d, shift=shift,
                defaults={'ward': ward, 'duty': duty, 'created_by': request.user})
            if not created:
                obj.ward, obj.duty = ward, duty
                obj.save(update_fields=['ward', 'duty'])
                messages.info(request, f"{nurse.get_full_name() or nurse.email} was already on that shift — updated.")
            else:
                messages.success(request, f"{nurse.get_full_name() or nurse.email} added to the roster.")
        else:
            messages.error(request, "Pick a nurse, ward, date and shift.")
    return redirect(f"{reverse('ipd:duty_roster')}?ward={ward_id}&week={week}")


@feature_required('ward_manage')
def roster_remove(request, pk):
    e = get_object_or_404(NurseShift, pk=pk)
    ward_id, week = e.ward_id, e.date.isoformat()
    if request.method == 'POST':
        e.delete()
        messages.success(request, "Removed from the roster.")
    return redirect(f"{reverse('ipd:duty_roster')}?ward={ward_id}&week={week}")


@feature_required('ward_manage')
def patient_allocation(request):
    """Allocate a ward's admitted patients among the nurses rostered for a shift."""
    wards = list(Ward.objects.all().order_by('name'))
    ward = next((w for w in wards if str(w.pk) == request.GET.get('ward')), wards[0] if wards else None)
    d = _parse_date(request.GET.get('date'), timezone.localdate())
    shifts = _shifts(request)
    shift = _pick_shift(request, shifts)

    admissions = (Admission.objects.filter(status='Admitted', bed__ward=ward)
                  .select_related('patient', 'bed') if ward else Admission.objects.none())
    rostered = (NurseShift.objects.filter(ward=ward, date=d, shift=shift).select_related('nurse')
                if ward else NurseShift.objects.none())
    nurses = [ns.nurse for ns in rostered]

    if request.method == 'POST':
        by_pk = {str(n.pk): n for n in nurses}
        for a in admissions:
            nid = request.POST.get(f"alloc_{a.pk}")
            if nid and nid in by_pk:
                PatientAllocation.objects.update_or_create(
                    admission=a, date=d, shift=shift,
                    defaults={'nurse': by_pk[nid], 'assigned_by': request.user})
            else:
                PatientAllocation.objects.filter(admission=a, date=d, shift=shift).delete()
        messages.success(request, "Patient allocation saved.")
        return redirect(f"{reverse('ipd:patient_allocation')}?ward={ward.pk if ward else ''}"
                        f"&date={d}&shift={shift.pk if shift else ''}")

    existing = {a.admission_id: a.nurse_id for a in
                PatientAllocation.objects.filter(date=d, shift=shift, admission__in=admissions)}
    load = {}
    for nid in existing.values():
        load[nid] = load.get(nid, 0) + 1
    rows = [{'admission': a, 'allocated_to': existing.get(a.pk)} for a in admissions]
    nurse_rows = [{'nurse': ns.nurse, 'duty': ns.duty, 'load': load.get(ns.nurse_id, 0)} for ns in rostered]
    return render(request, 'ipd/patient_allocation.html', {
        'wards': wards, 'ward': ward, 'date': d, 'shift': shift,
        'shifts': shifts, 'shift_time': shift.time_range if shift else '',
        'rows': rows, 'nurses': nurses, 'nurse_rows': nurse_rows,
    })


@feature_required('ward')
def my_duties(request):
    """A nurse's own upcoming shifts and the patients allotted to them today."""
    today = timezone.localdate()
    upcoming = (NurseShift.objects.filter(nurse=request.user, date__gte=today)
                .select_related('ward', 'shift').order_by('date', 'shift__order')[:20])
    my_alloc = (PatientAllocation.objects.filter(nurse=request.user, date=today)
                .select_related('admission__patient', 'admission__bed__ward', 'shift')
                .order_by('shift__order'))
    return render(request, 'ipd/my_duties.html', {
        'upcoming': upcoming, 'my_alloc': my_alloc, 'today': today,
        'current_shift': _current_shift(request),
    })


@feature_required('ward')
def vitals_add(request, pk):
    """Record a nursing vitals set (the TPR chart). Nurses do this; MEWS is scored
    on save and a red/amber score warns the nurse to escalate."""
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = VitalsObservationForm(request.POST)
        if form.is_valid():
            from .services import record_vitals
            m = record_vitals(admission, form, request.user).mews
            if m['band'] == 'RED':
                messages.warning(request, f"Vitals saved. MEWS {m['score']} — RED. {m['advice']}")
            elif m['band'] == 'AMBER':
                messages.info(request, f"Vitals saved. MEWS {m['score']} — AMBER. {m['advice']}")
            else:
                messages.success(request, f"Vitals saved. MEWS {m['score']} — routine.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = VitalsObservationForm()
    return render(request, 'ipd/vitals_form.html', {'form': form, 'admission': admission})


@feature_required('ward')
def fluid_add(request, pk):
    """Add an intake or output entry to the fluid balance chart."""
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = FluidBalanceEntryForm(request.POST)
        if form.is_valid():
            from .services import record_fluid
            record_fluid(admission, form, request.user)
            messages.success(request, "Fluid entry recorded.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = FluidBalanceEntryForm()
    return render(request, 'ipd/fluid_form.html', {'form': form, 'admission': admission})


@feature_required('ipd', 'ward')
def nursing_board(request):
    """The ward board: every inpatient with their latest MEWS, whether observations
    are overdue, and who is looking after them this shift — sorted so the sickest
    and the overdue rise to the top. The In-charge's 'who needs attention now'."""
    admissions = (_scoped_admissions(request).filter(status='Admitted')
                  .select_related('patient', 'bed__ward', 'attending_doctor')
                  .prefetch_related('observations'))
    ward_id = request.GET.get('ward')
    if ward_id:
        admissions = admissions.filter(bed__ward_id=ward_id)

    now = timezone.now()
    today = timezone.localdate()
    shift = _current_shift(request)
    allocs = {a.admission_id: a.nurse for a in
              PatientAllocation.objects.filter(date=today, shift=shift, admission__in=admissions)
              .select_related('nurse')}

    rows = []
    for adm in admissions:
        obs_list = list(adm.observations.all())
        obs = obs_list[0] if obs_list else None      # latest (Meta ordering -taken_at)
        hours = (now - obs.taken_at).total_seconds() / 3600.0 if obs else None
        overdue = obs is None or hours > OBS_INTERVAL_HOURS
        rows.append({
            'adm': adm, 'obs': obs, 'mews': obs.mews if obs else None,
            'overdue': overdue, 'hours': hours, 'nurse': allocs.get(adm.pk),
            'balance': fluid_totals(adm, today),
        })

    rank = {'RED': 0, 'AMBER': 1, 'GREEN': 2}

    def sort_key(r):
        band = r['mews']['band'] if r['mews'] else 'GREEN'
        return (rank[band], not r['overdue'], -(r['mews']['score'] if r['mews'] else -1))
    rows.sort(key=sort_key)

    return render(request, 'ipd/nursing_board.html', {
        'rows': rows, 'wards': list(Ward.objects.all().order_by('name')),
        'ward_id': ward_id, 'shift_label': shift.name if shift else '—',
        'interval': OBS_INTERVAL_HOURS, 'today': today,
    })


@feature_required('ward')
def nursing_note_add(request, pk):
    """Add a nurse's shift progress note."""
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = NursingNoteForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import record_nursing_note
            record_nursing_note(admission, form, request.user)
            messages.success(request, "Nursing note saved.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = NursingNoteForm(user=request.user,
                               initial={'shift': _current_shift(request)})
    return render(request, 'ipd/nursing_note_form.html', {'form': form, 'admission': admission})


@feature_required('ward')
def care_task_add(request, pk):
    """Log a routine care task (turning, hygiene, catheter care…)."""
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = CareTaskForm(request.POST)
        if form.is_valid():
            from .services import record_care_task
            record_care_task(admission, form, request.user)
            messages.success(request, "Care task logged.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = CareTaskForm()
    return render(request, 'ipd/care_task_form.html', {'form': form, 'admission': admission})


@feature_required('ward')
def handover_add(request, pk):
    """Write an SBAR end-of-shift handover for one patient."""
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = ShiftHandoverForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import record_handover
            record_handover(admission, form, request.user)
            messages.success(request, "Handover recorded.")
            return redirect('ipd:handover_board')
    else:
        form = ShiftHandoverForm(user=request.user,
                                 initial={'shift': _current_shift(request),
                                          'date': timezone.localdate()})
    return render(request, 'ipd/handover_form.html', {'form': form, 'admission': admission})


@feature_required('ward')
def handover_ack(request, pk):
    """Incoming nurse acknowledges a handover. Online-only — it records who took
    over and when, against live server state."""
    ho = get_object_or_404(ShiftHandover, pk=pk)
    # tenant safety: the handover's admission must be in the caller's scope
    get_object_or_404(_scoped_admissions(request), pk=ho.admission_id)
    if request.method == 'POST' and ho.acknowledged_by is None:
        ho.acknowledged_by = request.user
        ho.acknowledged_at = timezone.now()
        ho.save(update_fields=['acknowledged_by', 'acknowledged_at'])
        messages.success(request, "Handover acknowledged.")
    return redirect('ipd:handover_board')


@feature_required('ipd', 'ward')
def handover_board(request):
    """The shift handover screen: every inpatient with their most recent handover,
    for the nurse coming on. Unacknowledged handovers rise to the top."""
    admissions = (_scoped_admissions(request).filter(status='Admitted')
                  .select_related('patient', 'bed__ward')
                  .prefetch_related('handovers__from_nurse'))
    ward_id = request.GET.get('ward')
    if ward_id:
        admissions = admissions.filter(bed__ward_id=ward_id)
    rows = []
    for adm in admissions:
        hos = list(adm.handovers.all())
        rows.append({'adm': adm, 'handover': hos[0] if hos else None})
    rows.sort(key=lambda r: (r['handover'] is not None and r['handover'].acknowledged_by is not None,
                             r['handover'] is None))
    return render(request, 'ipd/handover_board.html', {
        'rows': rows, 'wards': list(Ward.objects.all().order_by('name')),
        'ward_id': ward_id, 'today': timezone.localdate(),
    })


@feature_required('ipd', 'ward')
def ward_census_view(request):
    """The daily ward census — admissions, discharges and occupancy for a date,
    per ward and hospital-wide. Computed from the admission records."""
    d = _parse_date(request.GET.get('date'), timezone.localdate())
    scoped = _scoped_admissions(request)
    overall = ward_census(scoped, d)
    wards = list(Ward.objects.all().order_by('name'))
    total_beds = Bed.objects.count()
    overall['total_beds'] = total_beds
    overall['occupancy_pct'] = round(100 * overall['currently_admitted'] / total_beds) if total_beds else 0

    per_ward = []
    for w in wards:
        ward_adm = scoped.filter(bed__ward=w)
        c = ward_census(ward_adm, d)
        beds = w.beds.count()
        c.update({'ward': w, 'beds': beds,
                  'occupancy_pct': round(100 * c['currently_admitted'] / beds) if beds else 0})
        per_ward.append(c)
    return render(request, 'ipd/ward_census.html', {
        'date': d, 'overall': overall, 'per_ward': per_ward,
        'prev_day': d - timedelta(days=1), 'next_day': d + timedelta(days=1),
    })
