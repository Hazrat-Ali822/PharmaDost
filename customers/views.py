from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required, feature_required
from .forms import CustomerForm, CustomerPaymentForm
from .models import Customer, CustomerPayment
from .services import record_payment

MANAGE_ROLES = ["ADMIN", "PHARMACIST", "WHOLESALE", "ACCOUNTANT", "RECEPTIONIST"]


@feature_required('customers')
def customer_list(request):
    q = request.GET.get("q", "").strip()
    ctype = request.GET.get("type", "").strip()
    customers = Customer.objects.all()
    if q:
        customers = customers.filter(
            Q(name__icontains=q) | Q(shop_name__icontains=q) | Q(phone__icontains=q) | Q(area__icontains=q)
        )
    if ctype in (Customer.RETAIL, Customer.WHOLESALE):
        customers = customers.filter(type=ctype)
    return render(request, "customers/customer_list.html", {"customers": customers, "q": q, "ctype": ctype})


@feature_required('customers')
def customer_create(request):
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Customer added successfully.")
            return redirect("customer_list")
    else:
        form = CustomerForm()
    return render(request, "customers/customer_form.html", {"form": form, "title": "Add Customer"})


@feature_required('customers')
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, "Customer updated successfully.")
            return redirect("customer_list")
    else:
        form = CustomerForm(instance=customer)
    return render(request, "customers/customer_form.html", {"form": form, "title": "Edit Customer"})


@feature_required('customers')
def customer_ledger(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sales = customer.sales.order_by("created_at") if hasattr(customer, "sales") else []
    payments = customer.payments.order_by("date", "created_at")

    # Build a combined, chronologically ordered ledger with a running balance.
    entries = []
    for s in sales:
        entries.append({
            "when": s.created_at,
            "kind": "SALE",
            "ref": f"Sale #{s.id}",
            "debit": s.total,          # customer owes
            "credit": s.paid,          # amount paid at sale time
        })
    for p in payments:
        entries.append({
            "when": p.created_at,
            "kind": "PAYMENT",
            "ref": f"{p.get_method_display()}" + (f" - {p.notes}" if p.notes else ""),
            "debit": 0,
            "credit": p.amount,
        })
    entries.sort(key=lambda e: e["when"])
    running = 0
    for e in entries:
        running += float(e["debit"]) - float(e["credit"])
        e["running"] = running

    return render(request, "customers/customer_ledger.html", {"customer": customer, "entries": entries})


@feature_required('customers')
def payment_create(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerPaymentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                record_payment(
                    customer,
                    amount=cd["amount"],
                    method=cd["method"],
                    notes=cd["notes"],
                    received_by=request.user,
                    date=cd["date"],
                )
                messages.success(request, "Payment recorded.")
                return redirect("customer_ledger", pk=customer.pk)
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = CustomerPaymentForm()
    return render(
        request,
        "customers/payment_form.html",
        {"form": form, "customer": customer, "title": f"Receive Payment - {customer}"},
    )
