from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required, role_required
from patients.models import Patient

from .forms import VaccineForm, VaccinationRecordForm
from .models import Vaccine, VaccinationRecord


@feature_required('vaccination')
def vaccination_list(request):
    if request.method == 'POST':
        form = VaccinationRecordForm(request.POST, user=request.user)
        if form.is_valid():
            rec = form.save(commit=False)
            rec.created_by = request.user
            rec.save()
            messages.success(request, 'Vaccination recorded.')
            return redirect('vaccination_list')
    else:
        form = VaccinationRecordForm(user=request.user)
    recent = VaccinationRecord.objects.select_related('patient', 'vaccine')[:100]
    return render(request, 'vaccination/list.html', {'form': form, 'recent': recent})


@feature_required('vaccination')
def due_list(request):
    today = timezone.localdate()
    due = VaccinationRecord.objects.select_related('patient', 'vaccine').filter(
        next_due_date__isnull=False, next_due_date__lte=today).order_by('next_due_date')
    return render(request, 'vaccination/due.html', {'due': due[:200], 'today': today})


@feature_required('vaccination')
def patient_card(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    records = patient.vaccinations.select_related('vaccine').all()
    return render(request, 'vaccination/card.html', {'patient': patient, 'records': records})


@feature_required('vaccination')
@role_required(['ADMIN'])
def vaccine_catalogue(request):
    q = (request.GET.get('q') or '').strip()
    vaccines = Vaccine.objects.all()
    if q:
        vaccines = vaccines.filter(Q(code__icontains=q) | Q(name__icontains=q))
    if request.method == 'POST':
        form = VaccineForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Vaccine added.')
            return redirect('vaccine_catalogue')
    else:
        form = VaccineForm()
    return render(request, 'vaccination/catalogue.html', {'vaccines': vaccines, 'form': form, 'q': q})
