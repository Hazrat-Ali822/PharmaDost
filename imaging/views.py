from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from pharma_mgmt.pagination import paginate
from django.views.decorators.http import require_POST

from accounts.decorators import role_required, feature_required, module_installed
from user_mgmt.models import current_currency
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
# Withdrawing a scan the patient refused — the radiology counter is where they say
# so, hence SONOGRAPHER; reception is left out (see lab.views.CANCEL_ROLES).
CANCEL_ROLES = ["ADMIN", "DOCTOR", "SONOGRAPHER"]


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
    # Fail-closed tenant scope + DOCTOR narrowing both live in _scoped_studies;
    # `if request.user.hospital:` here would leak every tenant's studies to a
    # hospital-less non-superuser.
    studies = (
        _scoped_studies(request)
        .select_related("patient", "referred_by", "performed_by")
        .order_by("-study_date")
    )
    modality = request.GET.get("modality", "").strip()
    if modality:
        studies = studies.filter(modality=modality)
    # A withdrawn scan is history, not work waiting to be done, so it leaves the
    # default list — but stays reachable, because "why was this never scanned" has
    # to remain answerable.
    show = request.GET.get("show", "active")
    if show == "cancelled":
        studies = studies.filter(status="Cancelled")
    else:
        studies = studies.exclude(status="Cancelled")
    page = paginate(request, studies)
    return render(
        request,
        "imaging/study_list.html",
        {"studies": page, "page_obj": page, "modality": modality, "show": show,
         "modalities": ImagingStudy.MODALITY_CHOICES},
    )


# Ward staff can refer an admitted patient for a scan (on the doctor's
# instruction) without getting the rest of the imaging module — writing REPORTS
# stays restricted to radiology/admin below.
@feature_required('imaging', 'ward')
def study_create(request):
    if request.method == "POST":
        form = ImagingStudyCreateForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import create_study
            study = create_study(form, request.user)
            inv = study.invoice
            # Sonographer/admin goes to the report screen; a doctor/receptionist who only
            # referred the scan gets a clean confirmation (not a 403 on a report-only page).
            can_report = request.user.is_superuser or getattr(request.user, "role", None) in REPORT_ROLES
            bill = f" Bill {inv.display_no} raised ({current_currency()} {inv.total}, unpaid)." if inv else ""
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
    # Same reason as lab.order_detail: never show a button that leads to a 403.
    can_cancel = (request.user.is_superuser
                  or getattr(request.user, "role", None) in CANCEL_ROLES)
    return render(request, "imaging/study_detail.html",
                  {"study": study, "can_cancel": can_cancel})


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


@role_required(CANCEL_ROLES)
def study_cancel(request, study_id):
    """Withdraw a scan the patient refused, taking its charge off the bill.

    A study is one scan, so there is no per-item case here as there is in the lab.
    See `imaging.services.cancel_study` for why a reported study is refused.
    """
    study = get_object_or_404(
        _scoped_studies(request).select_related("patient", "invoice"), pk=study_id)

    if request.method == "POST":
        from django.core.exceptions import ValidationError
        from .services import cancel_study
        try:
            money = cancel_study(study, user=request.user,
                                 reason=request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return render(request, "imaging/cancel_confirm.html",
                          {"study": study, "reason": request.POST.get("reason", "")})

        cur = current_currency()
        note = f"Study #{study.id} cancelled."
        if money["voided"]:
            note += f" Invoice {study.invoice.display_no} voided."
        elif money["removed"]:
            note += f" Charge removed — bill is now {cur} {study.invoice.total}."
        if money["refund_due"]:
            note += f" ⚠️ {cur} {money['refund_due']} already collected — refund it to the patient."
        messages.success(request, note)
        return redirect("imaging:study_detail", study_id=study.id)

    return render(request, "imaging/cancel_confirm.html", {"study": study, "reason": ""})


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
@module_installed('imaging')
def scan_catalog(request):
    """Admin price list for **this hospital's** imaging services.

    Scoped explicitly by `request.user.hospital` through `all_objects` — see the
    note on `lab.views.test_catalog`. It matters more here: this view also
    deletes by pk, so an unscoped queryset let one tenant's admin remove another
    tenant's scans outright, not merely reprice them.
    """
    hospital = request.user.hospital
    own_scans = ScanType.all_objects.filter(hospital=hospital)

    if request.method == "POST":
        if request.POST.get("add"):
            name = request.POST.get("name", "").strip()
            if name:
                ScanType.all_objects.create(
                    name=name, modality=request.POST.get("modality", "OTHER"),
                    price=_dec(request.POST.get("price")),
                    cost_price=_dec(request.POST.get("cost_price")),
                    hospital=hospital)
                messages.success(request, f"Added scan '{name}'.")
            else:
                messages.error(request, "Scan name is required.")
        elif request.POST.get("delete"):
            own_scans.filter(pk=request.POST.get("delete")).delete()
            messages.success(request, "Scan removed.")
        else:
            changed = 0
            for s in own_scans:
                key = f"price_{s.id}"
                if key in request.POST:
                    val = _dec(request.POST.get(key))
                    cost = _dec(request.POST.get(f"cost_{s.id}"))
                    active = f"active_{s.id}" in request.POST
                    if val != s.price or cost != s.cost_price or active != s.is_active:
                        s.price = val
                        s.cost_price = cost
                        s.is_active = active
                        s.save(update_fields=["price", "cost_price", "is_active"])
                        changed += 1
            messages.success(request, f"Updated {changed} scan(s).")
        return redirect("imaging:scan_catalog")

    return render(request, "imaging/scan_catalog.html",
                  {"scans": own_scans,
                   "modalities": ScanType.MODALITY_CHOICES})


@feature_required('imaging')
@require_POST
def collect_payment(request, study_id):
    study = get_object_or_404(_scoped_studies(request), pk=study_id)
    if study.is_cancelled:
        messages.error(request, "This scan was cancelled — there is nothing to collect.")
        return redirect('imaging:study_detail', study_id=study.id)
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
