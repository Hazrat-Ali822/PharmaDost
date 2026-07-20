from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import feature_required
from opd.models import Appointment
from .forms import PrescriptionForm, PrescriptionItemFormSet, RxPresetForm, RxPresetItemFormSet
from .models import Prescription, RxPreset, RxPresetItem


def _scoped_prescriptions(request):
    """Prescription has no hospital column — scope through the appointment's patient
    hospital, and restrict a doctor to their own patients' prescriptions.

    Fail CLOSED: every non-superuser is filtered by their own hospital even when
    that hospital is None (then they only see hospital-less rows, never another
    tenant's). Only superusers see across hospitals."""
    qs = Prescription.objects.all()
    if not request.user.is_superuser:
        qs = qs.filter(appointment__patient__hospital=request.user.hospital)
        if getattr(request.user, "role", None) == "DOCTOR":
            qs = qs.filter(appointment__doctor__user=request.user)
    return qs


def _scoped_appointments(request):
    """Same tenant/doctor scoping for the appointment a prescription is written against,
    so a user can't prescribe on another hospital's / another doctor's appointment."""
    qs = Appointment.objects.all()
    if not request.user.is_superuser:
        qs = qs.filter(patient__hospital=request.user.hospital)
        if getattr(request.user, "role", None) == "DOCTOR":
            qs = qs.filter(doctor__user=request.user)
    return qs


@feature_required('prescriptions')
def prescription_create(request, appointment_id):
    appointment = get_object_or_404(_scoped_appointments(request), pk=appointment_id)
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

                # ---- Update appointment status to DONE ----
                appointment.status = 'DONE'
                appointment.save()

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

    # Get presets
    presets = RxPreset.objects.all()
    if request.user.hospital:
        presets = presets.filter(hospital=request.user.hospital)

    import json
    presets_data = []
    for pr in presets.prefetch_related('items__medicine'):
        items_list = []
        for item in pr.items.all():
            items_list.append({
                'medicine_id': item.medicine.id,
                'medicine_name': item.medicine.name,
                'dosage': item.dosage,
                'duration_days': item.duration_days,
                'instructions': item.instructions,
            })
        presets_data.append({
            'id': pr.id,
            'name': pr.name,
            'items': items_list
        })
    presets_json = json.dumps(presets_data)

    return render(request, 'prescriptions/prescription_form.html', {
        'form': form,
        'med_formset': med_formset,
        'appointment': appointment,
        'title': 'Create Prescription',
        'presets': presets,
        'presets_json': presets_json,
    })


def _order_lab_tests(patient, tests, user):
    """Create a single TestOrder for the ticked tests + raise a pending invoice."""
    if not tests:
        return 0
    from lab.models import TestOrder, TestResult
    from billing.services import create_service_invoice
    from accounts.models import Notification
    
    order = TestOrder.objects.create(patient=patient, ordered_by=user)
    for t in tests:
        TestResult.objects.create(test_order=order, lab_test=t)
    inv = create_service_invoice(
        patient=patient,
        items=[(f"Lab: {t.name}", t.price) for t in tests],
        created_by=user,
    )
    if inv:
        order.invoice = inv
        order.save()
    
    # Notify Lab Technicians
    Notification.send_to_role(
        hospital=patient.hospital,
        role='LABTECH',
        message=f"🔬 New Lab Order: Patient '{patient.full_name}' has {len(tests)} test(s) pending.",
        link="/lab/orders/"
    )
    return len(tests)


def _order_scans(scan_types, patient, user):
    """Create an ImagingStudy per selected catalog scan + a pending invoice (catalog price)."""
    if not scan_types:
        return 0
    from imaging.models import ImagingStudy
    from billing.services import create_service_invoice
    from accounts.models import Notification
    
    for st in scan_types:
        study = ImagingStudy.objects.create(
            patient=patient, referred_by=user,
            modality=st.modality, study_name=st.name, price=st.price)
        inv = create_service_invoice(
            patient=patient,
            items=[(f"{study.get_modality_display()}: {study.study_name}", study.price)],
            created_by=user,
        )
        if inv:
            study.invoice = inv
            study.save()
        
    # Notify Sonographers / Radiologists
    Notification.send_to_role(
        hospital=patient.hospital,
        role='SONOGRAPHER',
        message=f"🩻 New Scan Study: Patient '{patient.full_name}' has scan(s) ordered.",
        link="/imaging/studies/"
    )
    return len(scan_types)


@feature_required('prescriptions')
def prescription_list(request):
    q = request.GET.get('q', '').strip()
    prescriptions = _scoped_prescriptions(request).select_related('appointment__patient', 'appointment__doctor').order_by('-created_at')

    if q:
        prescriptions = prescriptions.filter(
            Q(appointment__patient__full_name__icontains=q) |
            Q(appointment__patient__mrn__icontains=q) |
            Q(appointment__doctor__full_name__icontains=q) |
            Q(diagnosis__icontains=q)
        )
        
    return render(request, 'prescriptions/prescription_list.html', {
        'prescriptions': prescriptions,
        'q': q
    })


@feature_required('prescriptions')
def prescription_detail(request, pk):
    prescription = get_object_or_404(
        _scoped_prescriptions(request).select_related('appointment__patient', 'appointment__doctor').prefetch_related('items__medicine'),
        pk=pk
    )
    return render(request, 'prescriptions/prescription_detail.html', {'prescription': prescription})


@feature_required('prescriptions')
def prescription_edit(request, pk):
    prescription = get_object_or_404(
        _scoped_prescriptions(request).select_related('appointment__patient', 'appointment__doctor'),
        pk=pk
    )
    appointment = prescription.appointment
    
    if request.method == 'POST':
        form = PrescriptionForm(request.POST, instance=prescription)
        med_formset = PrescriptionItemFormSet(request.POST, instance=prescription, prefix='meds')
        if form.is_valid() and med_formset.is_valid():
            form.save()
            med_formset.save()
            messages.success(request, "Prescription updated successfully.")
            return redirect('prescription_detail', pk=prescription.pk)
    else:
        form = PrescriptionForm(instance=prescription)
        med_formset = PrescriptionItemFormSet(instance=prescription, prefix='meds')
        
    # Get presets
    presets = RxPreset.objects.all()
    if request.user.hospital:
        presets = presets.filter(hospital=request.user.hospital)

    import json
    presets_data = []
    for pr in presets.prefetch_related('items__medicine'):
        items_list = []
        for item in pr.items.all():
            items_list.append({
                'medicine_id': item.medicine.id,
                'medicine_name': item.medicine.name,
                'dosage': item.dosage,
                'duration_days': item.duration_days,
                'instructions': item.instructions,
            })
        presets_data.append({
            'id': pr.id,
            'name': pr.name,
            'items': items_list
        })
    presets_json = json.dumps(presets_data)

    return render(request, 'prescriptions/prescription_form.html', {
        'form': form,
        'med_formset': med_formset,
        'appointment': appointment,
        'prescription': prescription,
        'title': 'Edit Prescription',
        'is_edit': True,
        'presets': presets,
        'presets_json': presets_json,
    })


# --- Rx Presets Management ---

@feature_required('prescriptions')
def preset_list(request):
    presets = RxPreset.objects.all()
    if request.user.hospital:
        presets = presets.filter(hospital=request.user.hospital)
    return render(request, 'prescriptions/preset_list.html', {'presets': presets})


@feature_required('prescriptions')
def preset_create(request):
    if request.method == 'POST':
        form = RxPresetForm(request.POST)
        formset = RxPresetItemFormSet(request.POST, prefix='items')
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                preset = form.save(commit=False)
                if request.user.hospital:
                    preset.hospital = request.user.hospital
                else:
                    from saas.models import Hospital
                    preset.hospital = Hospital.objects.first()
                preset.save()
                formset.instance = preset
                formset.save()
            messages.success(request, "Rx Preset created successfully.")
            return redirect('prescription_presets')
    else:
        form = RxPresetForm()
        formset = RxPresetItemFormSet(prefix='items')
    return render(request, 'prescriptions/preset_form.html', {
        'form': form,
        'formset': formset,
        'title': 'Add Rx Preset'
    })


@feature_required('prescriptions')
def preset_edit(request, pk):
    preset = get_object_or_404(RxPreset, pk=pk)
    if request.method == 'POST':
        form = RxPresetForm(request.POST, instance=preset)
        formset = RxPresetItemFormSet(request.POST, instance=preset, prefix='items')
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, f"Rx Preset '{preset.name}' updated.")
            return redirect('prescription_presets')
    else:
        form = RxPresetForm(instance=preset)
        formset = RxPresetItemFormSet(instance=preset, prefix='items')
    return render(request, 'prescriptions/preset_form.html', {
        'form': form,
        'formset': formset,
        'preset': preset,
        'title': f'Edit {preset.name}'
    })


@feature_required('prescriptions')
def preset_delete(request, pk):
    preset = get_object_or_404(RxPreset, pk=pk)
    if request.method == 'POST':
        preset.delete()
        messages.success(request, "Rx Preset deleted.")
        return redirect('prescription_presets')
    return render(request, 'prescriptions/preset_confirm_delete.html', {'preset': preset})
