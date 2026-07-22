from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from accounts.decorators import feature_required, role_required
from accounts.models import Notification
from inventory.models import Medicine
from inventory.safety import screen_medicines
from patients.models import Patient
from .models import Ward, Bed, Admission, DoctorRound, MedicationLog, AdmissionRequest
from .forms import WardForm, BedForm, AdmissionForm, DoctorRoundForm, DischargeForm, MedicationLogForm

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
        form = AdmissionForm(request.POST)
        if form.is_valid():
            admission = form.save(commit=False)
            try:
                with transaction.atomic():
                    # Lock the chosen bed and re-check it is still free, so two
                    # receptionists can't admit two patients into the same bed.
                    bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
                    if bed.status != 'Available':
                        raise ValidationError(f"Bed {bed.bed_number} is no longer available.")
                    # A patient can only occupy one bed at a time.
                    if Admission.objects.filter(patient=admission.patient, status='Admitted').exists():
                        raise ValidationError(f"{admission.patient.full_name} already has an active admission.")
                    bed.status = 'Occupied'
                    bed.save(update_fields=['status'])
                    admission.save()
                    # close the originating advice, if any
                    if adm_req:
                        adm_req.status = 'Admitted'
                        adm_req.admission = admission
                        adm_req.save(update_fields=['status', 'admission'])
                messages.success(request, f"Patient {admission.patient.full_name} admitted successfully to Bed {bed.bed_number}.")
                return redirect('ipd:admission_detail', pk=admission.pk)
            except Bed.DoesNotExist:
                messages.error(request, "Selected bed was not found.")
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
        form = AdmissionForm(initial=initial)
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

    return render(request, 'ipd/admission_detail.html', {
        'admission': admission,
        'rounds': rounds,
        'medication_logs': medication_logs,
        'medicine_total': medicine_total,
        'prescriptions': prescriptions,
        'lab_orders': lab_orders,
        'imaging_studies': imaging_studies,
    })

@feature_required('ipd', 'ward')
def medication_log_add(request, pk):
    admission = get_object_or_404(_scoped_admissions(request), pk=pk)
    if request.method == 'POST':
        form = MedicationLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.admission = admission
            log.administered_by = request.user
            medicine = form.cleaned_data.get('medicine')

            if medicine is None:
                # Off-catalogue drug: recorded on the chart, but nothing to take
                # from stock and nothing to bill.
                log.unit_price = Decimal('0.00')
                log.save()
            else:
                # Stock and money move together, on a locked row (see CLAUDE.md).
                try:
                    with transaction.atomic():
                        med = Medicine.objects.select_for_update().get(pk=medicine.pk)
                        med.reduce_stock(log.quantity)
                        log.medicine = med
                        # Freeze the price of the day — the catalogue may change
                        # before this patient is discharged and billed.
                        log.unit_price = med.price or Decimal('0.00')
                        if not log.medicine_name:
                            log.medicine_name = f"{med.name} ({med.brand})" if med.brand else med.name
                        log.save()
                except ValueError as exc:
                    form.add_error(None, str(exc))
                    return render(request, 'ipd/medication_form.html',
                                  _medication_ctx(form, admission))

            # Allergy check happens AFTER recording: the dose is already given, so
            # the chart must reflect reality — the warning is for the staff to act on.
            if medicine is not None:
                for warning in screen_medicines(admission.patient, [medicine]):
                    messages.warning(request, warning)

            if log.charge:
                messages.success(
                    request,
                    f"{log.medicine_name} x{log.quantity} recorded — stock reduced, "
                    f"Rs {log.charge} added to the discharge bill."
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
        'pharmacy_medicines': _pharmacy_medicines(),
    }


def _pharmacy_medicines():
    """Catalogue rows for the medicine-search box on the ward medication form.

    Tenant-scoped by `Medicine`'s manager. `batches` is prefetched because the
    template shows sellable stock per row, which reads them (see CLAUDE.md).
    """
    return (Medicine.objects.filter(is_active=True)
            .prefetch_related('batches')
            .order_by('name', 'brand'))

@feature_required('ipd', 'ward')
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
            with transaction.atomic():
                adm = form.save(commit=False)
                adm.status = 'Discharged'
                adm.discharge_date = timezone.now()
                adm.save()

                # Free the bed (locked so a concurrent admission can't clobber it)
                bed = Bed.objects.select_for_update().get(pk=adm.bed_id)
                bed.status = 'Available'
                bed.save(update_fields=['status'])

                # Bed charges = calendar days the bed was occupied, counting the
                # admission day and the discharge day (inclusive), minimum one day —
                # this is how hospital room bills are normally itemised.
                days = (adm.discharge_date.date() - adm.admission_date.date()).days + 1
                if days < 1:
                    days = 1
                est_bed_charges = days * adm.bed.ward.daily_rate

                from billing.services import create_service_invoice
                items = [
                    (f"IPD Bed Charges: Bed {adm.bed.bed_number} ({adm.bed.ward.name}) — {days} Day(s)", est_bed_charges),
                ]

                # Everything the ward gave this patient from pharmacy stock. Without
                # this the discharge bill was bed charges only, so every dose
                # administered during the stay was given away free.
                med_total = Decimal('0.00')
                for log in adm.medication_logs.select_related('medicine').all():
                    if log.charge:
                        items.append((
                            f"Medicine: {log.medicine_name} x{log.quantity}",
                            log.charge,
                        ))
                        med_total += log.charge

                create_service_invoice(
                    patient=adm.patient,
                    items=items,
                    created_by=request.user,
                    paid=0,
                )

            if med_total:
                messages.success(
                    request,
                    f"Patient {adm.patient.full_name} discharged. Invoice generated: "
                    f"bed charges Rs {est_bed_charges} + medicines Rs {med_total}."
                )
            else:
                messages.success(request, f"Patient {adm.patient.full_name} has been discharged. Bed charges invoice generated.")
            return redirect('ipd:admission_detail', pk=adm.pk)
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
