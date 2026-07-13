from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from accounts.decorators import feature_required
from .models import Ward, Bed, Admission, DoctorRound
from .forms import WardForm, BedForm, AdmissionForm, DoctorRoundForm, DischargeForm

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
            bed = admission.bed
            bed.status = 'Occupied'
            bed.save()
            admission.save()
            messages.success(request, f"Patient {admission.patient.full_name} admitted successfully to Bed {bed.bed_number}.")
            return redirect('ipd:admission_detail', pk=admission.pk)
    else:
        form = AdmissionForm()
    return render(request, 'ipd/admission_form.html', {
        'form': form,
        'title': 'Admit New Patient'
    })

@feature_required('ipd')
def admission_detail(request, pk):
    admission = get_object_or_404(Admission.objects.select_related('patient', 'bed__ward', 'attending_doctor'), pk=pk)
    rounds = admission.rounds.all().order_by('-round_time')
    return render(request, 'ipd/admission_detail.html', {
        'admission': admission,
        'rounds': rounds,
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
            adm = form.save(commit=False)
            adm.status = 'Discharged'
            adm.discharge_date = timezone.now()
            adm.save()
            
            # Free the bed
            bed = adm.bed
            bed.status = 'Available'
            bed.save()
            
            # Auto-calculate bed charges and create billing invoice
            days = (timezone.now() - adm.admission_date).days
            if days == 0:
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
        
    # Calculate days stayed
    days = (timezone.now() - admission.admission_date).days
    if days == 0:
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
