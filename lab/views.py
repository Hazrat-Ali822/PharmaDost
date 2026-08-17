# lab/views.py
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404

from pharma_mgmt.pagination import paginate
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_POST

from django.core.exceptions import ValidationError

from accounts.decorators import role_required, feature_required, module_installed
from user_mgmt.models import current_currency
from .models import TestOrder, LabTest, TestCategory, TestResult
from .forms import TestOrderCreateForm, TestResultFormSet
from billing.models import PatientPayment


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
# Withdrawing a test the patient refused. The lab counter is where the patient
# actually says no, so LABTECH is included — but not RECEPTIONIST, who never has
# that conversation and would only be guessing at a clinical decision.
CANCEL_ROLES = ["ADMIN", "DOCTOR", "LABTECH"]


def _scoped_orders(request):
    """TestOrder has no hospital column of its own — scope through the patient's
    hospital, and restrict a doctor to the orders they placed. Mirrors order_list
    so detail/report/results/payment can't be reached cross-tenant by guessing ids."""
    qs = TestOrder.objects.all()
    if not request.user.is_superuser:
        # fail closed: a hospital-less non-superuser sees only hospital-less rows,
        # never another tenant's orders
        qs = qs.filter(patient__hospital=request.user.hospital)
        if getattr(request.user, "role", None) == "DOCTOR":
            qs = qs.filter(ordered_by=request.user)
    return qs


@feature_required('lab')
def order_list(request):
    # Fail-closed tenant scope (TestOrder has no hospital column) — and the DOCTOR
    # narrowing — both live in _scoped_orders. Using `if request.user.hospital:`
    # here would leak every tenant's orders to a hospital-less non-superuser.
    orders = (
        _scoped_orders(request)
        .select_related("patient", "ordered_by")
        # results__lab_test, not just results: every row renders `total_price`,
        # which walks each result's LabTest for its price.
        .prefetch_related("results__lab_test")
        .order_by("-order_date")
    )

    show = request.GET.get('show', 'pending')
    if show == 'pending':
        orders = orders.filter(status='Pending')
    elif show == 'completed':
        # 'Cancelled' is excluded explicitly: a withdrawn order is not work the lab
        # finished, and a bare exclude(status='Pending') would file it as done.
        orders = orders.exclude(status__in=['Pending', 'Cancelled'])
    elif show == 'cancelled':
        orders = orders.filter(status='Cancelled')


    page = paginate(request, orders)
    return render(request, "lab/order_list.html",
                  {"orders": page, "page_obj": page, "show": show})


# Ward staff can raise an order for an admitted patient (on the doctor's
# instruction) without getting the rest of the lab module — entering RESULTS stays
# restricted to lab/admin below.
@feature_required('lab', 'ward')
def order_create(request):
    if request.method == "POST":
        form = TestOrderCreateForm(request.POST, user=request.user)
        if form.is_valid():
            from .services import create_test_order
            order = create_test_order(form, request.user)
            inv = order.invoice
            # Whoever can enter results (lab/admin) goes straight to the results screen;
            # a doctor/receptionist who only *ordered* the test gets a clean confirmation
            # instead of being bounced to a lab-only page (which would 403).
            can_result = request.user.is_superuser or getattr(request.user, "role", None) in RESULT_ROLES
            bill = f" Bill {inv.display_no} raised ({current_currency()} {inv.total}, unpaid)." if inv else ""
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
        _scoped_orders(request).select_related("patient", "ordered_by")
        .prefetch_related("results__lab_test"),
        pk=order_id
    )
    # Hide the Cancel buttons from anyone the cancel views would 403 — a link
    # straight into a 403 is the trap CLAUDE.md warns about for nav links.
    can_cancel = (request.user.is_superuser
                  or getattr(request.user, "role", None) in CANCEL_ROLES)
    return render(request, "lab/order_detail.html",
                  {"order": order, "can_cancel": can_cancel})


@role_required(RESULT_ROLES)
def order_results_edit(request, order_id):
    order = get_object_or_404(_scoped_orders(request), pk=order_id)
    if order.is_cancelled:
        messages.error(request, "This order was cancelled — results can no longer be entered.")
        return redirect("lab:order_detail", order_id=order.id)
    # A cancelled test is not work waiting to be done, so it is kept out of the
    # results form entirely — otherwise the lab types a value into a test the
    # patient refused and it silently becomes chargeable again.
    live = order.results.filter(is_cancelled=False)
    if request.method == "POST":
        formset = TestResultFormSet(request.POST, instance=order, queryset=live)
        if formset.is_valid():
            formset.save()
            # Auto-complete if every result still live has a value
            all_filled = all((r.result_value or "").strip()
                             for r in order.results.filter(is_cancelled=False))
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
        formset = TestResultFormSet(instance=order, queryset=live)
    return render(request, "lab/order_results_edit.html", {"order": order, "formset": formset})


@role_required(RESULT_ROLES)
def order_mark_completed(request, order_id):
    order = get_object_or_404(_scoped_orders(request), pk=order_id)
    order.status = "Completed"
    order.save(update_fields=["status"])
    messages.success(request, "Order marked as Completed.")
    return redirect("lab:order_detail", order_id=order.id)


def _cancel_screen(request, *, order, target, action_url):
    """Shared confirm-and-give-a-reason page for both cancel paths."""
    return render(request, "lab/cancel_confirm.html", {
        "order": order, "target": target, "action_url": action_url,
        "reason": request.POST.get("reason", ""),
    })


@role_required(CANCEL_ROLES)
def test_cancel(request, order_id, result_id):
    """Withdraw ONE test from an order — the '3 tests ordered, patient wants 2' case.

    The charge comes off the invoice with it; see `lab.services.cancel_test` for
    why the row is kept rather than deleted, and why an already-resulted test is
    refused here.
    """
    order = get_object_or_404(_scoped_orders(request).select_related("patient"), pk=order_id)
    result = get_object_or_404(
        TestResult.objects.select_related("lab_test"), pk=result_id, test_order=order)
    action_url = reverse("lab:test_cancel", args=[order.id, result.id])

    if request.method == "POST":
        from .services import cancel_test
        try:
            money = cancel_test(result, user=request.user,
                                reason=request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return _cancel_screen(request, order=order, target=result, action_url=action_url)

        cur = current_currency()
        note = f"{result.lab_test.name} cancelled."
        if money["voided"]:
            note += f" Nothing left on the bill — invoice {order.invoice.display_no} voided."
        elif money["removed"]:
            note += f" Charge removed — bill is now {cur} {order.invoice.total}."
        if money["refund_due"]:
            note += f" ⚠️ {cur} {money['refund_due']} already collected — refund it to the patient."
        messages.success(request, note)
        return redirect("lab:order_detail", order_id=order.id)

    return _cancel_screen(request, order=order, target=result, action_url=action_url)


@role_required(CANCEL_ROLES)
def order_cancel(request, order_id):
    """Withdraw a whole order — the patient refused every test on it."""
    order = get_object_or_404(
        _scoped_orders(request).select_related("patient", "invoice"), pk=order_id)
    action_url = reverse("lab:order_cancel", args=[order.id])

    if request.method == "POST":
        from .services import cancel_order
        try:
            money = cancel_order(order, user=request.user,
                                 reason=request.POST.get("reason", ""))
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return _cancel_screen(request, order=order, target=None, action_url=action_url)

        cur = current_currency()
        note = f"Order #{order.id} cancelled."
        if money["voided"]:
            note += f" Invoice {order.invoice.display_no} voided."
        if money["refund_due"]:
            note += f" ⚠️ {cur} {money['refund_due']} already collected — refund it to the patient."
        messages.success(request, note)
        return redirect("lab:order_detail", order_id=order.id)

    return _cancel_screen(request, order=order, target=None, action_url=action_url)


@feature_required('lab')
def order_report(request, order_id):
    order = get_object_or_404(
        _scoped_orders(request).select_related("patient", "ordered_by"),
        pk=order_id
    )
    return render(request, "lab/order_report.html", {"order": order})


@feature_required('catalog')
@module_installed('lab')
def test_catalog(request):
    """Admin price list for **this hospital's** lab tests.

    Scoped explicitly by `request.user.hospital` through `all_objects`, rather
    than left to `LabTest.objects`: `TenantManager` deliberately lets a superuser
    through unfiltered, and this view bulk-writes prices — which is how it came to
    rewrite every tenant's price list at once. On the desktop/LAN build the admin
    *is* a hospital-less superuser, and `hospital=None` matches that install's own
    rows, so it keeps working there unchanged.
    """
    hospital = request.user.hospital
    own_tests = LabTest.all_objects.filter(hospital=hospital)
    own_categories = TestCategory.all_objects.filter(hospital=hospital)

    if request.method == "POST":
        if request.POST.get("add"):
            name = request.POST.get("name", "").strip()
            new_cat = request.POST.get("new_category", "").strip()
            cat_id = request.POST.get("category")
            if name and (new_cat or cat_id):
                if new_cat:
                    category, _ = TestCategory.all_objects.get_or_create(
                        name=new_cat, hospital=hospital)
                else:
                    category = get_object_or_404(own_categories, pk=cat_id)
                LabTest.all_objects.create(
                    category=category, name=name, price=_dec(request.POST.get("price")),
                    unit=request.POST.get("unit", "").strip(),
                    normal_range=request.POST.get("normal_range", "").strip(),
                    hospital=hospital)
                messages.success(request, f"Added lab test '{name}'.")
            else:
                messages.error(request, "Test name and a category are required.")
        else:
            changed = 0
            for t in own_tests:
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

    categories = own_categories.order_by("name")
    groups = [(c, own_tests.filter(category=c).order_by("name")) for c in categories]
    return render(request, "lab/test_catalog.html",
                  {"groups": groups, "categories": categories})


@feature_required('lab')
@require_POST
def collect_payment(request, order_id):
    order = get_object_or_404(_scoped_orders(request), pk=order_id)
    if order.is_cancelled:
        messages.error(request, "This order was cancelled — there is nothing to collect.")
        return redirect('lab:order_detail', order_id=order.id)
    if order.payment_status == 'Pending':
        # order.total_price, not the `tests` M2M: a cancelled test must not be charged.
        total_price = order.total_price
        order.payment_status = 'Paid'
        order.payment_collected_by = request.user
        order.payment_amount = total_price
        order.save()

        if order.invoice:
            invoice = order.invoice
            invoice.paid = invoice.total
            invoice.save()

        PatientPayment.objects.create(
            patient=order.patient,
            amount=total_price,
            payment_method='CASH',
            note=f"Collected by Lab counter for Order #{order.id}",
            collected_by=request.user,
            hospital=request.user.hospital
        )
        messages.success(request, f"Collected Rs. {total_price} successfully for Order #{order.id}!")
    else:
        messages.info(request, "Payment has already been collected.")
    return redirect('lab:order_list')
