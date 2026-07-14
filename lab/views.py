# lab/views.py
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages

from accounts.decorators import role_required, feature_required
from .models import TestOrder, LabTest, TestCategory
from .forms import TestOrderCreateForm, TestResultFormSet


def _dec(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError):
        return Decimal("0")

# Doctor can *recommend* (order) a test; lab tech can also create orders.
ORDER_ROLES = ["ADMIN", "DOCTOR", "LABTECH", "RECEPTIONIST"]
# Entering / verifying results is the lab's job.
RESULT_ROLES = ["ADMIN", "LABTECH"]
# Viewing a report: anyone clinically involved.
VIEW_ROLES = ["ADMIN", "DOCTOR", "LABTECH", "RECEPTIONIST"]


@feature_required('lab')
def order_list(request):
    orders = (
        TestOrder.objects
        .select_related("patient", "ordered_by")
        .prefetch_related("results")
        .order_by("-order_date")
    )
    if request.user.hospital:
        orders = orders.filter(patient__hospital=request.user.hospital)
        
    show = request.GET.get('show', 'pending')
    if show == 'pending':
        orders = orders.filter(status='Pending')
    elif show == 'completed':
        orders = orders.exclude(status='Pending')
        
    return render(request, "lab/order_list.html", {"orders": orders, "show": show})


@feature_required('lab')
def order_create(request):
    if request.method == "POST":
        form = TestOrderCreateForm(request.POST, user=request.user)
        if form.is_valid():
            order = form.save()
            # auto-generate a pending bill for the ordered tests
            from billing.services import create_service_invoice
            items = [(f"Lab: {r.lab_test.name}", r.lab_test.price)
                     for r in order.results.select_related("lab_test")]
            inv = create_service_invoice(
                patient=order.patient, items=items, created_by=request.user)
            # Whoever can enter results (lab/admin) goes straight to the results screen;
            # a doctor/receptionist who only *ordered* the test gets a clean confirmation
            # instead of being bounced to a lab-only page (which would 403).
            can_result = request.user.is_superuser or getattr(request.user, "role", None) in RESULT_ROLES
            bill = f" Bill #{inv.id} raised (Rs {inv.total}, unpaid)." if inv else ""
            tail = "Add results now." if can_result else "Sent to the lab — they will enter the results."
            messages.success(request, f"Order #{order.id} created.{bill} {tail}")
            if can_result:
                return redirect("lab:order_results_edit", order_id=order.id)
            return redirect("lab:order_detail", order_id=order.id)
    else:
        # allow ?patient=<pk> so a doctor can order straight from a patient page
        initial = {}
        patient_id = request.GET.get("patient")
        if patient_id:
            initial["patient"] = patient_id
        form = TestOrderCreateForm(user=request.user, initial=initial)
    return render(request, "lab/order_create.html", {"form": form})


@feature_required('lab')
def order_detail(request, order_id):
    order = get_object_or_404(
        TestOrder.objects.select_related("patient", "ordered_by"),
        pk=order_id
    )
    return render(request, "lab/order_detail.html", {"order": order})


@role_required(RESULT_ROLES)
def order_results_edit(request, order_id):
    order = get_object_or_404(TestOrder, pk=order_id)
    if request.method == "POST":
        formset = TestResultFormSet(request.POST, instance=order)
        if formset.is_valid():
            formset.save()
            # Auto-complete if every result has a value
            all_filled = all((r.result_value or "").strip() for r in order.results.all())
            if all_filled:
                order.status = "Completed"
                order.save(update_fields=["status"])
            messages.success(request, "Results saved.")
            # "Save & Print" -> jump straight to the printable report (auto-opens print)
            if "save_print" in request.POST:
                return redirect(
                    reverse("lab:order_report", args=[order.id]) + "?print=1"
                )
            return redirect("lab:order_detail", order_id=order.id)
    else:
        formset = TestResultFormSet(instance=order)
    return render(request, "lab/order_results_edit.html", {"order": order, "formset": formset})


@role_required(RESULT_ROLES)
def order_mark_completed(request, order_id):
    order = get_object_or_404(TestOrder, pk=order_id)
    order.status = "Completed"
    order.save(update_fields=["status"])
    messages.success(request, "Order marked as Completed.")
    return redirect("lab:order_detail", order_id=order.id)


@feature_required('lab')
def order_report(request, order_id):
    order = get_object_or_404(
        TestOrder.objects.select_related("patient", "ordered_by"),
        pk=order_id
    )
    return render(request, "lab/order_report.html", {"order": order})


@feature_required('catalog')
def test_catalog(request):
    """Admin price list for lab tests — set/adjust prices in bulk and add new tests."""
    if request.method == "POST":
        if request.POST.get("add"):
            name = request.POST.get("name", "").strip()
            new_cat = request.POST.get("new_category", "").strip()
            cat_id = request.POST.get("category")
            if name and (new_cat or cat_id):
                if new_cat:
                    category, _ = TestCategory.objects.get_or_create(name=new_cat)
                else:
                    category = get_object_or_404(TestCategory, pk=cat_id)
                LabTest.objects.create(
                    category=category, name=name, price=_dec(request.POST.get("price")),
                    unit=request.POST.get("unit", "").strip(),
                    normal_range=request.POST.get("normal_range", "").strip())
                messages.success(request, f"Added lab test '{name}'.")
            else:
                messages.error(request, "Test name and a category are required.")
        else:
            changed = 0
            for t in LabTest.objects.all():
                if f"price_{t.id}" not in request.POST:
                    continue
                price = _dec(request.POST.get(f"price_{t.id}"))
                unit = request.POST.get(f"unit_{t.id}", "").strip()
                nrange = request.POST.get(f"nr_{t.id}", "").strip()   # normal range (optional)
                if price != t.price or unit != t.unit or nrange != t.normal_range:
                    t.price, t.unit, t.normal_range = price, unit, nrange
                    t.save(update_fields=["price", "unit", "normal_range"])
                    changed += 1
            messages.success(request, f"Updated {changed} test(s).")
        return redirect("lab:test_catalog")

    categories = TestCategory.objects.order_by("name")
    groups = [(c, LabTest.objects.filter(category=c).order_by("name")) for c in categories]
    return render(request, "lab/test_catalog.html",
                  {"groups": groups, "categories": categories})
