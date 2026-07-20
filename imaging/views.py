from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required, feature_required
from .forms import ImagingReportForm, ImagingStudyCreateForm
from .models import ImagingStudy, ScanType
from billing.models import PatientPayment


def _dec(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError):
        return Decimal("0")

VIEW_ROLES = ["ADMIN", "DOCTOR", "SONOGRAPHER", "RECEPTIONIST"]
ORDER_ROLES = ["ADMIN", "DOCTOR", "RECEPTIONIST", "SONOGRAPHER"]
REPORT_ROLES = ["ADMIN", "SONOGRAPHER"]


def _scoped_studies(request):
    """ImagingStudy has no hospital column of its own — scope through the patient's
    hospital, and restrict a doctor to the studies they referred. Mirrors study_list
    so detail/report/payment can't be reached cross-tenant by guessing ids."""
    qs = ImagingStudy.objects.all()
    if not request.user.is_superuser:
        # fail closed: a hospital-less non-superuser sees only hospital-less rows,
        # never another tenant's studies
        qs = qs.filter(patient__hospital=request.user.hospital)
        if getattr(request.user, "role", None) == "DOCTOR":
            qs = qs.filter(referred_by=request.user)
    return qs


@feature_required('imaging')
def study_list(request):
    studies = (
        ImagingStudy.objects.select_related("patient", "referred_by", "performed_by")
        .order_by("-study_date")
    )
    if request.user.hospital:
        studies = studies.filter(patient__hospital=request.user.hospital)
    if getattr(request.user, 'role', None) == 'DOCTOR':
        studies = studies.filter(referred_by=request.user)
    modality = request.GET.get("modality", "").strip()
    if modality:
        studies = studies.filter(modality=modality)
    return render(
        request,
        "imaging/study_list.html",
        {"studies": studies, "modality": modality,
         "modalities": ImagingStudy.MODALITY_CHOICES},
    )


@feature_required('imaging')
def study_create(request):
    if request.method == "POST":
        form = ImagingStudyCreateForm(request.POST, user=request.user)
        if form.is_valid():
            study = form.save()
            # auto-generate a pending bill for the scan fee
            from billing.services import create_service_invoice
            inv = create_service_invoice(
                patient=study.patient,
                items=[(f"{study.get_modality_display()}: {study.study_name}", study.price)],
                created_by=request.user)
            if inv:
                study.invoice = inv
                study.save()
            # Sonographer/admin goes to the report screen; a doctor/receptionist who only
            # referred the scan gets a clean confirmation (not a 403 on a report-only page).
            can_report = request.user.is_superuser or getattr(request.user, "role", None) in REPORT_ROLES
            bill = f" Bill #{inv.id} raised (Rs {inv.total}, unpaid)." if inv else ""
            tail = "Add the report now." if can_report else "Sent to radiology — they will add the report."
            messages.success(request, f"Study #{study.id} registered.{bill} {tail}")
            if can_report:
                return redirect("imaging:study_report_edit", study_id=study.id)
            return redirect("imaging:study_detail", study_id=study.id)
    else:
        # allow ?patient=<pk> so a doctor can order a scan straight from a patient page
        initial = {}
        patient_id = request.GET.get("patient")
        if patient_id:
            initial["patient"] = patient_id
        form = ImagingStudyCreateForm(user=request.user, initial=initial)
    return render(request, "imaging/study_create.html", {"form": form})


@feature_required('imaging')
def study_detail(request, study_id):
    study = get_object_or_404(
        _scoped_studies(request).select_related("patient", "referred_by", "performed_by"),
        pk=study_id,
    )
    return render(request, "imaging/study_detail.html", {"study": study})


@role_required(REPORT_ROLES)
def study_report_edit(request, study_id):
    study = get_object_or_404(_scoped_studies(request), pk=study_id)
    if request.method == "POST":
        form = ImagingReportForm(request.POST, request.FILES, instance=study, user=request.user)
        if form.is_valid():
            study = form.save()
            messages.success(request, "Report saved.")
            return redirect("imaging:study_detail", study_id=study.id)
    else:
        form = ImagingReportForm(instance=study, user=request.user)
    return render(request, "imaging/study_report_edit.html", {"study": study, "form": form})


@role_required(REPORT_ROLES)
def study_mark_delivered(request, study_id):
    study = get_object_or_404(_scoped_studies(request), pk=study_id)
    study.status = "Delivered"
    study.save(update_fields=["status"])
    messages.success(request, "Report marked as delivered.")
    return redirect("imaging:study_detail", study_id=study.id)


@feature_required('imaging')
def study_report(request, study_id):
    study = get_object_or_404(
        _scoped_studies(request).select_related("patient", "referred_by", "performed_by"),
        pk=study_id,
    )
    return render(request, "imaging/study_report.html", {"study": study})


@feature_required('catalog')
def scan_catalog(request):
    """Admin price list for imaging services (scans) — set prices, add/remove scans."""
    if request.method == "POST":
        if request.POST.get("add"):
            name = request.POST.get("name", "").strip()
            if name:
                ScanType.objects.create(
                    name=name, modality=request.POST.get("modality", "OTHER"),
                    price=_dec(request.POST.get("price")))
                messages.success(request, f"Added scan '{name}'.")
            else:
                messages.error(request, "Scan name is required.")
        elif request.POST.get("delete"):
            ScanType.objects.filter(pk=request.POST.get("delete")).delete()
            messages.success(request, "Scan removed.")
        else:
            changed = 0
            for s in ScanType.objects.all():
                key = f"price_{s.id}"
                if key in request.POST:
                    val = _dec(request.POST.get(key))
                    active = f"active_{s.id}" in request.POST
                    if val != s.price or active != s.is_active:
                        s.price = val
                        s.is_active = active
                        s.save(update_fields=["price", "is_active"])
                        changed += 1
            messages.success(request, f"Updated {changed} scan(s).")
        return redirect("imaging:scan_catalog")

    return render(request, "imaging/scan_catalog.html",
                  {"scans": ScanType.objects.all(),
                   "modalities": ScanType.MODALITY_CHOICES})


@feature_required('imaging')
@require_POST
def collect_payment(request, study_id):
    study = get_object_or_404(_scoped_studies(request), pk=study_id)
    if study.payment_status == 'Pending':
        study.payment_status = 'Paid'
        study.payment_collected_by = request.user
        study.payment_amount = study.price
        study.save()

        if study.invoice:
            invoice = study.invoice
            invoice.paid = invoice.total
            invoice.save()

        PatientPayment.objects.create(
            patient=study.patient,
            amount=study.price,
            payment_method='CASH',
            note=f"Collected by Radiology counter for Study #{study.id}",
            collected_by=request.user,
            hospital=request.user.hospital
        )
        messages.success(request, f"Collected Rs. {study.price} successfully for Study #{study.id}!")
    else:
        messages.info(request, "Payment has already been collected.")
    return redirect('imaging:study_list')
