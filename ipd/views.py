from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from accounts.decorators import feature_required
from .models import Ward, Bed, Admission, DoctorRound, MedicationLog
from .forms import WardForm, BedForm, AdmissionForm, DoctorRoundForm, DischargeForm, MedicationLogForm

@feature_required('ipd')
def admission_list(request):
    active_admissions = Admission.objects.filter(status='Admitted').select_related('patient', 'bed__ward', 'attending_doctor')
    past_admissions = Admission.objects.filter(status='Discharged').select_related('patient', 'bed__ward', 'attending_doctor').order_by('-discharge_date')[:50]
    return render(request, 'ipd/admission_list.html', {
        'active_admissions': active_admissions,
        'past_admissions': past_admissions,
    })

@feature_required('ipd')
def admission_create(request):
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
        form = AdmissionForm(initial=initial)
    return render(request, 'ipd/admission_form.html', {
        'form': form,
        'title': 'Admit New Patient'
    })

@feature_required('ipd')
def admission_detail(request, pk):
    admission = get_object_or_404(Admission.objects.select_related('patient', 'bed__ward', 'attending_doctor'), pk=pk)
    rounds = admission.rounds.all().order_by('-round_time')
    medication_logs = admission.medication_logs.all().select_related('administered_by').order_by('-administered_at')
    return render(request, 'ipd/admission_detail.html', {
        'admission': admission,
        'rounds': rounds,
        'medication_logs': medication_logs,
    })

@feature_required('ipd')
def medication_log_add(request, pk):
    admission = get_object_or_404(Admission, pk=pk)
    if request.method == 'POST':
        form = MedicationLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.admission = admission
            log.administered_by = request.user
            log.save()
            messages.success(request, f"Medication '{log.medicine_name}' logged successfully.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = MedicationLogForm()
    return render(request, 'ipd/medication_form.html', {
        'form': form,
        'admission': admission,
    })

@feature_required('ipd')
def doctor_round_add(request, pk):
    admission = get_object_or_404(Admission, pk=pk)
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
    admission = get_object_or_404(Admission, pk=pk)
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
                create_service_invoice(
                    patient=adm.patient,
                    items=items,
                    created_by=request.user,
                    paid=0,
                )

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

@feature_required('ipd')
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
