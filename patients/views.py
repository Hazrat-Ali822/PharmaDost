from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required, feature_required
from .forms import PatientForm, ClinicalRecordForm
from .models import Patient


def _visible_patients(user):
    """Doctors only see patients assigned to them (via appointments);
    everyone else (admin/reception/lab/etc.) sees all."""
    qs = Patient.objects.all()
    role = getattr(user, 'role', None)
    if role == 'DOCTOR' and not user.is_superuser:
        qs = qs.filter(appointments__doctor__user=user).distinct()
    return qs


@feature_required('patients')
def patient_list(request):
    q = request.GET.get('q', '').strip()
    patients = _visible_patients(request.user)
    if q:
        patients = patients.filter(
            Q(full_name__icontains=q) |
            Q(phone__icontains=q) |
            Q(mrn__icontains=q) |
            Q(cnic__icontains=q)
        )
    is_doctor = getattr(request.user, 'role', None) == 'DOCTOR' and not request.user.is_superuser
    return render(request, 'patients/patient_list.html',
                  {'patients': patients, 'q': q, 'is_doctor': is_doctor})


@feature_required('patients')
@role_required(['ADMIN', 'RECEPTIONIST'])
def patient_create(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Patient registered successfully.')
            return redirect('patient_list')
    else:
        form = PatientForm()
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Register Patient'})


@feature_required('patients')
def patient_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            messages.success(request, 'Patient updated successfully.')
            return redirect('patient_list')
    else:
        form = PatientForm(instance=patient)
    return render(request, 'patients/patient_form.html', {'form': form, 'title': 'Edit Patient'})


def _get_scoped_patient(request, pk):
    """404 if not found, 403 if a doctor tries to open a patient not assigned to them."""
    patient = get_object_or_404(Patient, pk=pk)
    if not _visible_patients(request.user).filter(pk=pk).exists():
        raise PermissionDenied("You can only view your own patients.")
    return patient


@feature_required('patients')
def patient_detail(request, pk):
    from prescriptions.models import Prescription
    patient = _get_scoped_patient(request, pk)

    appointments = patient.appointments.select_related('doctor').order_by('-appointment_date', '-created_at')
    prescriptions = (Prescription.objects
                     .filter(appointment__patient=patient)
                     .select_related('appointment', 'appointment__doctor')
                     .prefetch_related('items', 'items__medicine')
                     .order_by('-created_at'))
    lab_orders = (patient.lab_orders
                  .prefetch_related('results', 'results__lab_test')
                  .order_by('-order_date'))
    clinical = patient.clinical_records.select_related('doctor', 'created_by').all()
    imaging_studies = (patient.imaging_studies
                       .select_related('referred_by', 'performed_by')
                       .order_by('-study_date'))
    invoices = (patient.invoices.prefetch_related('items').order_by('-created_at')
                if hasattr(patient, 'invoices') else [])
    pharmacy_sales = (patient.pharmacy_sales
                      .prefetch_related('items', 'items__medicine')
                      .order_by('-created_at'))

    return render(request, 'patients/patient_detail.html', {
        'patient': patient,
        'appointments': appointments,
        'prescriptions': prescriptions,
        'lab_orders': lab_orders,
        'clinical': clinical,
        'imaging_studies': imaging_studies,
        'invoices': invoices,
        'pharmacy_sales': pharmacy_sales,
    })


@role_required(["ADMIN", "DOCTOR"])
def record_add(request, pk):
    patient = _get_scoped_patient(request, pk)
    if request.method == 'POST':
        form = ClinicalRecordForm(request.POST)
        if form.is_valid():
            rec = form.save(commit=False)
            rec.patient = patient
            rec.created_by = request.user
            # link the doctor profile if the logged-in user is a doctor
            doctor = getattr(request.user, 'doctor', None)
            if doctor is not None:
                rec.doctor = doctor
            rec.save()
            messages.success(request, 'Clinical record added to history.')
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = ClinicalRecordForm()
    return render(request, 'patients/record_form.html', {'form': form, 'patient': patient})
