from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from accounts.decorators import feature_required, role_required
from accounts.models import Notification
from patients.models import Patient
from .models import SurgeryCategory, SurgeryProcedure, SurgeryRecord, SurgeryRequest
from .forms import SurgeryCategoryForm, SurgeryProcedureForm, SurgeryRecordForm

@feature_required('ot')
def surgery_list(request):
    now = timezone.now()
    # Active/Scheduled surgeries (start time in future or no end time)
    upcoming_surgeries = SurgeryRecord.objects.filter(
        Q(start_time__gt=now) | Q(end_time__isnull=True)
    ).select_related('patient', 'procedure', 'lead_surgeon').order_by('start_time')
    
    # Completed surgeries
    completed_surgeries = SurgeryRecord.objects.filter(
        end_time__isnull=False, start_time__lte=now
    ).select_related('patient', 'procedure', 'lead_surgeon').order_by('-end_time')[:50]
    
    return render(request, 'ot/surgery_list.html', {
        'upcoming_surgeries': upcoming_surgeries,
        'completed_surgeries': completed_surgeries,
    })

@feature_required('ot')
def surgery_create(request):
    # optional: scheduling a doctor's surgery advice (from the OT queue)
    req_id = request.GET.get('request_id') or request.POST.get('request_id')
    surg_req = SurgeryRequest.objects.filter(pk=req_id, status='Pending').first() if req_id else None

    if request.method == 'POST':
        form = SurgeryRecordForm(request.POST)
        if form.is_valid():
            from billing.services import create_service_invoice
            with transaction.atomic():
                record = form.save()

                # Create billing invoice for the surgery procedure (atomic with the
                # record — never leave a surgery saved but unbilled, or vice-versa).
                items = [
                    (f"OT Surgery: {record.procedure.name} (Surgeon: Dr. {record.lead_surgeon.full_name})", record.procedure.standard_charge),
                ]
                create_service_invoice(
                    patient=record.patient,
                    items=items,
                    created_by=request.user,
                    paid=0,
                )
                # close the originating advice, if any
                if surg_req:
                    surg_req.status = 'Scheduled'
                    surg_req.surgery = record
                    surg_req.save(update_fields=['status', 'surgery'])

            messages.success(request, f"Surgery for {record.patient.full_name} scheduled successfully. Surgery invoice generated.")
            return redirect('ot:surgery_detail', pk=record.pk)
    else:
        initial = {}
        if surg_req:
            initial['patient'] = surg_req.patient
            if surg_req.procedure_id:
                initial['procedure'] = surg_req.procedure
        form = SurgeryRecordForm(initial=initial)
    return render(request, 'ot/surgery_form.html', {
        'form': form,
        'title': 'Schedule New Surgery',
        'request_id': req_id or '',
        'surg_req': surg_req,
    })

@feature_required('ot')
def surgery_detail(request, pk):
    record = get_object_or_404(SurgeryRecord.objects.select_related('patient', 'admission', 'procedure', 'lead_surgeon'), pk=pk)
    return render(request, 'ot/surgery_detail.html', {
        'record': record,
    })

@feature_required('ot')
def surgery_edit(request, pk):
    record = get_object_or_404(SurgeryRecord, pk=pk)
    if request.method == 'POST':
        form = SurgeryRecordForm(request.POST, instance=record)
        if form.is_valid():
            rec = form.save()
            messages.success(request, "Surgery log updated successfully.")
            return redirect('ot:surgery_detail', pk=rec.pk)
    else:
        form = SurgeryRecordForm(instance=record)
    return render(request, 'ot/surgery_form.html', {
        'form': form,
        'title': 'Update Surgery Log'
    })

@feature_required('ot')
def procedure_list(request):
    categories = SurgeryCategory.objects.prefetch_related('procedures').all()
    return render(request, 'ot/procedure_list.html', {
        'categories': categories,
    })

@feature_required('ot')
def category_create(request):
    if request.method == 'POST':
        form = SurgeryCategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Category '{category.name}' registered.")
            return redirect('ot:procedure_list')
    else:
        form = SurgeryCategoryForm()
    return render(request, 'ot/category_form.html', {
        'form': form,
        'title': 'Create Surgery Category'
    })

@feature_required('ot')
def procedure_create(request):
    if request.method == 'POST':
        form = SurgeryProcedureForm(request.POST)
        if form.is_valid():
            proc = form.save()
            messages.success(request, f"Surgery procedure '{proc.name}' registered.")
            return redirect('ot:procedure_list')
    else:
        form = SurgeryProcedureForm()
    return render(request, 'ot/procedure_form.html', {
        'form': form,
        'title': 'Add Surgery Procedure'
    })


# ---------------------------------------------------------------------------
# Surgery advice (doctor -> OT / reception handoff)
# ---------------------------------------------------------------------------

@feature_required('patients')
@role_required(['ADMIN', 'DOCTOR'])
def surgery_advise(request, patient_id):
    """A doctor advises that this patient needs surgery. Creates a pending request
    and notifies the OT / reception desk, who then schedule it."""
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        proc_id = request.POST.get('procedure') or None
        urgency = request.POST.get('urgency', 'Elective')
        if not reason:
            messages.error(request, 'Please enter the indication / reason for surgery.')
        else:
            SurgeryRequest.objects.create(
                patient=patient, advised_by=request.user, reason=reason,
                procedure_id=proc_id or None, urgency=urgency)
            for role in ('RECEPTIONIST', 'ADMIN'):
                Notification.send_to_role(
                    hospital=patient.hospital, role=role,
                    message=f"🔪 Surgery advised ({urgency}): {patient.full_name} — please schedule.",
                    link='/ot/requests/')
            messages.success(request, f"Surgery advised for {patient.full_name}. OT/reception has been notified.")
            return redirect('patient_detail', pk=patient.pk)
    return render(request, 'ot/surgery_advise.html', {
        'patient': patient,
        'procedures': SurgeryProcedure.objects.select_related('category').order_by('name'),
        'urgencies': SurgeryRequest.URGENCY_CHOICES,
    })


@feature_required('ot')
def surgery_request_list(request):
    """OT / reception queue of pending surgery advices to schedule."""
    pending = (SurgeryRequest.objects.filter(status='Pending')
               .select_related('patient', 'advised_by', 'procedure')
               .order_by('created_at'))
    recent = (SurgeryRequest.objects.exclude(status='Pending')
              .select_related('patient', 'surgery')
              .order_by('-created_at')[:20])
    return render(request, 'ot/surgery_request_list.html', {'pending': pending, 'recent': recent})


@feature_required('ot')
def surgery_request_cancel(request, pk):
    req = get_object_or_404(SurgeryRequest, pk=pk)
    if request.method == 'POST':
        req.status = 'Cancelled'
        req.save(update_fields=['status'])
        messages.info(request, 'Surgery request cancelled.')
    return redirect('ot:surgery_request_list')
