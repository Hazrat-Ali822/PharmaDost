from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required, role_required

from .forms import DiagnosisCodeForm, PatientDiagnosisForm
from .models import DiagnosisCode, PatientDiagnosis


@feature_required('diagnosis')
def diagnosis_list(request):
    """Recent coded diagnoses across the tenant + an add form."""
    if request.method == 'POST':
        form = PatientDiagnosisForm(request.POST, user=request.user)
        if form.is_valid():
            dx = form.save(commit=False)
            dx.created_by = request.user
            dx.save()
            messages.success(request, 'Diagnosis recorded.')
            return redirect('diagnosis_list')
    else:
        form = PatientDiagnosisForm(user=request.user)
    recent = PatientDiagnosis.objects.select_related('patient', 'code', 'doctor')[:100]
    return render(request, 'diagnosis/list.html', {'form': form, 'recent': recent})


@feature_required('diagnosis')
def patient_diagnoses(request, patient_id):
    from patients.models import Patient
    patient = get_object_or_404(Patient, pk=patient_id)
    dxs = patient.diagnoses.select_related('code', 'doctor').all()
    return render(request, 'diagnosis/patient.html', {'patient': patient, 'dxs': dxs})


@feature_required('diagnosis')
@role_required(['ADMIN'])
def code_catalogue(request):
    q = (request.GET.get('q') or '').strip()
    codes = DiagnosisCode.objects.all()
    if q:
        codes = codes.filter(Q(code__icontains=q) | Q(title__icontains=q) | Q(category__icontains=q))
    if request.method == 'POST':
        form = DiagnosisCodeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'ICD code added.')
            return redirect('diagnosis_catalogue')
    else:
        form = DiagnosisCodeForm()
    return render(request, 'diagnosis/catalogue.html', {'codes': codes[:300], 'form': form, 'q': q})
