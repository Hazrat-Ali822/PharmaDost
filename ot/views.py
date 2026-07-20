from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from accounts.decorators import feature_required
from .models import SurgeryCategory, SurgeryProcedure, SurgeryRecord
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

            messages.success(request, f"Surgery for {record.patient.full_name} scheduled successfully. Surgery invoice generated.")
            return redirect('ot:surgery_detail', pk=record.pk)
    else:
        form = SurgeryRecordForm()
    return render(request, 'ot/surgery_form.html', {
        'form': form,
        'title': 'Schedule New Surgery'
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
