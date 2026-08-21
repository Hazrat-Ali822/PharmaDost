from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import denied, feature_required, role_required
from accounts.permissions import can_handle_prescriptions
from opd.models import Appointment
from .forms import PrescriptionForm, PrescriptionItemFormSet, RxPresetForm, RxPresetItemFormSet
from .models import Prescription, PrescriptionItem, RxPreset, RxPresetItem

# Declining a prescribed medicine. The pharmacist is included because the counter
# is where the patient actually says "I don't want this one" — but they hold `pos`,
# not `prescriptions`, so the feature gate has to accept either key.
CANCEL_ROLES = ['ADMIN', 'DOCTOR', 'PHARMACIST']


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


def _scoped_presets(request):
    """RxPreset has a hospital FK and doctor FK.
    Scope by hospital. If user is a DOCTOR (or role DOCTOR), show only THEIR presets
    (or presets where doctor is None / hospital-wide).
    """
    qs = RxPreset.objects.all()
    if not request.user.is_superuser:
        qs = qs.filter(hospital=request.user.hospital)
        if getattr(request.user, "role", None) == "DOCTOR":
            try:
                from opd.models import Doctor
                doc = Doctor.objects.filter(user=request.user).first()
                if doc:
                    qs = qs.filter(Q(doctor=doc) | Q(doctor__isnull=True))
                else:
                    qs = qs.filter(doctor__isnull=True)
            except Exception:
                pass
    return qs


@feature_required('prescriptions')
def prescription_create(request, appointment_id):
    appointment = get_object_or_404(_scoped_appointments(request), pk=appointment_id)
    patient = appointment.patient

    if request.method == 'POST':
        form = PrescriptionForm(request.POST)
        med_formset = PrescriptionItemFormSet(request.POST, prefix='meds')
        if form.is_valid() and med_formset.is_valid():
            from .services import save_prescription
            res = save_prescription(appointment, form, med_formset, request.user)
            n_meds, n_tests, n_img = len(res.medicines), res.n_tests, res.n_scans
            rx_warnings = res.warnings

            parts = [f"{n_meds} medicine(s)"]
            if n_tests:
                parts.append(f"{n_tests} lab test(s) sent to the lab")
            if n_img:
                parts.append(f"{n_img} scan(s) sent to radiology")
            messages.success(request, "Prescription saved — " + ", ".join(parts) + ".")
            for w in rx_warnings:
                messages.warning(request, "⚠️ " + w)
            return redirect('patient_detail', pk=patient.pk)
    else:
        form = PrescriptionForm()
        med_formset = PrescriptionItemFormSet(prefix='meds')

    # Get presets (scoped to this specific doctor & hospital)
    presets = _scoped_presets(request)

    import json
    presets_data = []
    try:
        for pr in presets.prefetch_related('items__medicine'):
            items_list = []
            for item in pr.items.all():
                c_name = getattr(item, 'custom_medicine_name', '') or ''
                items_list.append({
                    'medicine_id': item.medicine.id if item.medicine else None,
                    'medicine_name': item.medicine.name if item.medicine else c_name,
                    'custom_medicine_name': c_name,
                    'dosage': item.dosage or '',
                    'duration_days': item.duration_days or 3,
                    'instructions': item.instructions or '',
                })
            presets_data.append({
                'id': pr.id,
                'name': pr.name,
                'complaint': getattr(pr, 'complaint', '') or '',
                'diagnosis': getattr(pr, 'diagnosis', '') or '',
                'notes': getattr(pr, 'notes', '') or '',
                'items': items_list
            })
    except Exception:
        presets_data = []
    presets_json = json.dumps(presets_data)

    # Fetch most recent previous prescription for 1-click repeat/clone
    prev_rx = _scoped_prescriptions(request).filter(appointment__patient=patient).exclude(appointment=appointment).order_by('-id').first()
    prev_rx_json = "[]"
    if prev_rx:
        prev_items = []
        for item in prev_rx.items.all():
            prev_items.append({
                'medicine_id': item.medicine.id if item.medicine else None,
                'medicine_name': item.medicine.name if item.medicine else (item.custom_medicine_name or ''),
                'custom_medicine_name': item.custom_medicine_name or '',
                'dosage': item.dosage or '',
                'duration_days': item.duration_days or 5,
                'instructions': item.instructions or '',
            })
        prev_rx_json = json.dumps(prev_items)

    return render(request, 'prescriptions/prescription_form.html', {
        'form': form,
        'med_formset': med_formset,
        'appointment': appointment,
        'title': 'Create Prescription',
        'presets': presets,
        'presets_json': presets_json,
        'prev_rx': prev_rx,
        'prev_rx_json': prev_rx_json,
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
        service='LAB',
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
            service='IMAGING',
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
        
    from pharma_mgmt.pagination import paginate
    page = paginate(request, prescriptions)
    return render(request, 'prescriptions/prescription_list.html', {
        'prescriptions': page,
        'page_obj': page,
        'q': q
    })


# 'pos' as well as 'prescriptions': the pharmacist is the one standing in front of
# the patient when they decline a medicine, and they hold `pos`, not `prescriptions`.
# This shows them nothing new — the POS already pre-loads the Rx contents into the cart.
@feature_required('prescriptions', 'pos')
def prescription_detail(request, pk):
    if not can_handle_prescriptions(request.user):
        return denied(request)
    prescription = get_object_or_404(
        _scoped_prescriptions(request).select_related('appointment__patient', 'appointment__doctor').prefetch_related('items__medicine'),
        pk=pk
    )
    can_cancel = (request.user.is_superuser
                  or getattr(request.user, 'role', None) in CANCEL_ROLES)
    return render(request, 'prescriptions/prescription_detail.html',
                  {'prescription': prescription, 'can_cancel': can_cancel})


@feature_required('prescriptions', 'pos')
def prescription_labels(request, pk):
    """Printable dosage stickers — one label per prescribed medicine (name, dosage,
    duration, instructions, patient) to stick on the dispensed pack."""
    if not can_handle_prescriptions(request.user):
        return denied(request)
    prescription = get_object_or_404(
        _scoped_prescriptions(request)
        .select_related('appointment__patient', 'appointment__doctor')
        .prefetch_related('items__medicine'),
        pk=pk)
    return render(request, 'prescriptions/labels_print.html', {'prescription': prescription})


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
    presets = _scoped_presets(request)

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


@feature_required('prescriptions', 'pos')
@role_required(CANCEL_ROLES)
def item_cancel(request, pk, item_id):
    """The patient declined one medicine on the Rx.

    No bill is touched: a medicine is charged when it is dispensed at the POS, not
    when it is prescribed, so refusing it simply means it is never sold. What this
    fixes is the queue — without it the Rx sits PENDING for ever waiting for a
    medicine nobody is coming back for.
    """
    if not can_handle_prescriptions(request.user):
        return denied(request)
    prescription = get_object_or_404(
        _scoped_prescriptions(request).select_related('appointment__patient'), pk=pk)
    item = get_object_or_404(
        PrescriptionItem.objects.select_related('medicine'),
        pk=item_id, prescription=prescription)

    if request.method == 'POST':
        from .services import cancel_item
        try:
            cancel_item(item, user=request.user, reason=request.POST.get('reason', ''))
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return render(request, 'prescriptions/cancel_confirm.html', {
                'prescription': prescription, 'item': item,
                'reason': request.POST.get('reason', '')})
        messages.success(request, f"{item.display_name} marked as declined by the patient.")
        return redirect('prescription_detail', pk=prescription.pk)

    return render(request, 'prescriptions/cancel_confirm.html',
                  {'prescription': prescription, 'item': item, 'reason': ''})


@feature_required('prescriptions', 'pos')
@role_required(CANCEL_ROLES)
def prescription_cancel(request, pk):
    """The patient declined the whole prescription (or the doctor withdrew it)."""
    if not can_handle_prescriptions(request.user):
        return denied(request)
    prescription = get_object_or_404(
        _scoped_prescriptions(request).select_related('appointment__patient'), pk=pk)

    if request.method == 'POST':
        from .services import cancel_prescription
        try:
            n = cancel_prescription(prescription, user=request.user,
                                    reason=request.POST.get('reason', ''))
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return render(request, 'prescriptions/cancel_confirm.html', {
                'prescription': prescription, 'item': None,
                'reason': request.POST.get('reason', '')})
        messages.success(
            request,
            f"Prescription #{prescription.pk} cancelled — {n} medicine(s) withdrawn "
            f"from the pharmacy queue.")
        return redirect('prescription_detail', pk=prescription.pk)

    return render(request, 'prescriptions/cancel_confirm.html',
                  {'prescription': prescription, 'item': None, 'reason': ''})


# --- Rx Presets Management ---

@feature_required('prescriptions')
def preset_list(request):
    presets = _scoped_presets(request)
    return render(request, 'prescriptions/preset_list.html', {'presets': presets})


@feature_required('prescriptions')
def preset_create(request):
    if request.method == 'POST':
        form = RxPresetForm(request.POST)
        formset = RxPresetItemFormSet(request.POST, prefix='items')
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                preset = form.save(commit=False)
                # No `or Hospital.objects.first()`. That filed a hospital-less
                # user's preset into whichever tenant had the lowest id — a real
                # customer's data — and on the desktop build, where there are no
                # hospitals at all, it was None against a NOT NULL column and the
                # save simply crashed. `saas.signals.auto_assign_hospital` stamps
                # it from the thread-local, and a hospital-less install correctly
                # gets NULL.
                if getattr(request.user, 'role', None) == 'DOCTOR':
                    from opd.models import Doctor
                    preset.doctor = Doctor.objects.filter(user=request.user).first()
                preset.save()
                formset.instance = preset
                formset.save()
            messages.success(request, f"Rx Preset '{preset.name}' created successfully.")
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
    preset = get_object_or_404(_scoped_presets(request), pk=pk)
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
    preset = get_object_or_404(_scoped_presets(request), pk=pk)
    if request.method == 'POST':
        preset.delete()
        messages.success(request, "Rx Preset deleted.")
        return redirect('prescription_presets')
    return render(request, 'prescriptions/preset_confirm_delete.html', {'preset': preset})
