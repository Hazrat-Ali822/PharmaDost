from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import feature_required
from opd.models import Appointment
from .forms import PrescriptionForm, PrescriptionItemFormSet
from .models import Prescription


@feature_required('prescriptions')
def prescription_create(request, appointment_id):
    appointment = get_object_or_404(Appointment, pk=appointment_id)
    patient = appointment.patient

    if request.method == 'POST':
        form = PrescriptionForm(request.POST)
        med_formset = PrescriptionItemFormSet(request.POST, prefix='meds')
        if form.is_valid() and med_formset.is_valid():
            with transaction.atomic():
                prescription = form.save(commit=False)
                prescription.appointment = appointment
                prescription.save()

                # ---- medicines (many) ----
                med_formset.instance = prescription
                meds = med_formset.save()  # blank extra rows are skipped automatically
                n_meds = len(meds)

                # ---- lab tests -> one order + a pending bill ----
                tests = list(form.cleaned_data.get('tests') or [])
                n_tests = _order_lab_tests(patient, tests, request.user)

                # ---- scans -> each study + a pending bill (priced from the catalog) ----
                scans = list(form.cleaned_data.get('scans') or [])
                n_img = _order_scans(scans, patient, request.user)

            parts = [f"{n_meds} medicine(s)"]
            if n_tests:
                parts.append(f"{n_tests} lab test(s) sent to the lab")
            if n_img:
                parts.append(f"{n_img} scan(s) sent to radiology")
            messages.success(request, "Prescription saved — " + ", ".join(parts) + ".")
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = PrescriptionForm()
        med_formset = PrescriptionItemFormSet(prefix='meds')

    return render(request, 'prescriptions/prescription_form.html', {
        'form': form,
        'med_formset': med_formset,
        'appointment': appointment,
        'title': 'Create Prescription',
    })


def _order_lab_tests(patient, tests, user):
    """Create a single TestOrder for the ticked tests + raise a pending invoice."""
    if not tests:
        return 0
    from lab.models import TestOrder, TestResult
    from billing.services import create_service_invoice
    order = TestOrder.objects.create(patient=patient, ordered_by=user)
    for t in tests:
        TestResult.objects.create(test_order=order, lab_test=t)
    create_service_invoice(
        patient=patient,
        items=[(f"Lab: {t.name}", t.price) for t in tests],
        created_by=user,
    )
    return len(tests)


def _order_scans(scan_types, patient, user):
    """Create an ImagingStudy per selected catalog scan + a pending invoice (catalog price)."""
    if not scan_types:
        return 0
    from imaging.models import ImagingStudy
    from billing.services import create_service_invoice
    for st in scan_types:
        study = ImagingStudy.objects.create(
            patient=patient, referred_by=user,
            modality=st.modality, study_name=st.name, price=st.price)
        create_service_invoice(
            patient=patient,
            items=[(f"{study.get_modality_display()}: {study.study_name}", study.price)],
            created_by=user,
        )
    return len(scan_types)


@feature_required('prescriptions')
def prescription_list(request):
    prescriptions = Prescription.objects.select_related('appointment__patient').all()
    return render(request, 'prescriptions/prescription_list.html', {'prescriptions': prescriptions})
