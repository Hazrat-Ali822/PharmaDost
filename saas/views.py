from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.db import models
from django.utils.text import slugify
from .models import Hospital, HospitalPayment, PlatformExpense
from accounts.models import User
from accounts.permissions import MODULES

# Decorator to ensure only superuser can access SaaS management
def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url='/login/')(view_func)

def _by_hospital(qs, value):
    """One grouped query -> {hospital_id: aggregate}. Keeps per-tenant stats flat
    (a fixed handful of queries) instead of one query per hospital in a loop."""
    return {row['hospital_id']: row['v']
            for row in qs.values('hospital_id').annotate(v=value)}


@superuser_required
def saas_dashboard(request):
    from patients.models import Patient
    from sales.models import Sale

    today = timezone.localdate()
    month_start = today.replace(day=1)

    hospitals = list(Hospital.objects.all().order_by('-created_at'))

    # Per-tenant intelligence: each of these is a SINGLE grouped query over all
    # tenants, built into a dict and attached in Python — no query in the loop.
    users_by = _by_hospital(User.objects.all(), models.Count('id'))
    patients_by = _by_hospital(Patient.objects.all(), models.Count('id'))
    sales_by = _by_hospital(Sale.objects.all(), models.Count('id'))
    revenue_by = _by_hospital(
        Sale.objects.filter(created_at__date__gte=month_start), models.Sum('total'))
    last_sale_by = _by_hospital(Sale.objects.all(), models.Max('created_at'))

    for h in hospitals:
        h.n_users = users_by.get(h.id, 0)
        h.n_patients = patients_by.get(h.id, 0)
        h.n_sales = sales_by.get(h.id, 0)
        h.rev_month = revenue_by.get(h.id) or 0
        h.last_sale = last_sale_by.get(h.id)
        h.days_left = (h.expiry_date - today).days
        if not h.is_active:
            h.status = 'blocked'
        elif h.days_left < 0:
            h.status = 'expired'
        elif h.days_left <= 7:
            h.status = 'expiring'
        else:
            h.status = 'active'

    payments = (HospitalPayment.objects.select_related('hospital')
                .order_by('-payment_date')[:10])
    expenses = PlatformExpense.objects.all().order_by('-expense_date')[:10]

    # Platform-wide metrics
    active_count = sum(1 for h in hospitals if h.is_active)
    attention_count = sum(1 for h in hospitals if h.status in ('expired', 'expiring', 'blocked'))
    projected_income = sum((h.monthly_price for h in hospitals if h.is_active), 0)
    total_received = HospitalPayment.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    total_expense = PlatformExpense.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    net_profit = total_received - total_expense

    context = {
        'hospitals': hospitals,
        'payments': payments,
        'expenses': expenses,
        'active_count': active_count,
        'hospital_count': len(hospitals),
        'attention_count': attention_count,
        'total_patients': sum(patients_by.values()),
        'total_sales': sum(sales_by.values()),
        'total_users': sum(users_by.values()),
        'projected_income': projected_income,
        'total_received': total_received,
        'total_expense': total_expense,
        'net_profit': net_profit,
    }
    return render(request, 'saas/dashboard.html', context)

@superuser_required
def hospital_create(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        slug = request.POST.get('slug')
        monthly_price = request.POST.get('monthly_price')
        expiry_date = request.POST.get('expiry_date')
        is_active = request.POST.get('is_active') == 'on'
        selected_modules = request.POST.getlist('modules')

        admin_email = request.POST.get('admin_email')
        admin_password = request.POST.get('admin_password')

        if not slug:
            slug = slugify(name)

        if Hospital.objects.filter(slug=slug).exists():
            messages.error(request, f"A hospital with slug '{slug}' already exists.")
            return render(request, 'saas/hospital_form.html', {'modules': MODULES})

        if User.objects.filter(email=admin_email).exists():
            messages.error(request, f"User with email '{admin_email}' already exists.")
            return render(request, 'saas/hospital_form.html', {'modules': MODULES})

        # 1. Create Hospital
        hospital = Hospital.objects.create(
            name=name,
            slug=slug,
            monthly_price=monthly_price,
            expiry_date=expiry_date,
            is_active=is_active,
            enabled_modules=selected_modules
        )

        # 2. Create Hospital Admin Account
        admin_user = User.objects.create_user(email=admin_email, password=admin_password)
        admin_user.role = 'ADMIN'
        admin_user.hospital = hospital
        admin_user.save()

        messages.success(request, f"Hospital '{name}' and Admin account '{admin_email}' created successfully!")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_form.html', {'modules': MODULES})

@superuser_required
def hospital_edit(request, pk):
    hospital = get_object_or_404(Hospital, pk=pk)
    if request.method == 'POST':
        hospital.name = request.POST.get('name')
        hospital.monthly_price = request.POST.get('monthly_price')
        hospital.expiry_date = request.POST.get('expiry_date')
        hospital.is_active = request.POST.get('is_active') == 'on'
        hospital.enabled_modules = request.POST.getlist('modules')
        hospital.save()

        messages.success(request, f"Hospital '{hospital.name}' updated successfully!")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_form.html', {'hospital': hospital, 'modules': MODULES})

@superuser_required
def hospital_delete(request, pk):
    """Permanently delete a hospital and EVERYTHING under it. Superuser-only,
    and guarded by a type-the-name confirmation because it cannot be undone.

    `purge_tenant` wipes every hospital-scoped row first, in dependency-safe
    passes — a bare `hospital.delete()` cannot, because child rows hold PROTECT
    FKs to their parents (SaleItem→Medicine, Invoice→Patient, Sale→Customer, …)
    and the cascade raises `ProtectedError` on any tenant that has actually
    traded. `User.hospital` is SET_NULL, so staff would otherwise linger as
    orphaned accounts that can still sign in: we capture their ids first and
    delete them once everything that referenced them is gone."""
    from django.db import transaction
    from patients.models import Patient
    from sales.models import Sale
    from billing.models import Invoice

    hospital = get_object_or_404(Hospital, pk=pk)
    stats = {
        'patients': Patient.objects.filter(hospital=hospital).count(),
        'staff': User.objects.filter(hospital=hospital, is_superuser=False).count(),
        'sales': Sale.objects.filter(hospital=hospital).count(),
        'invoices': Invoice.objects.filter(hospital=hospital).count(),
    }

    if request.method == 'POST':
        typed = (request.POST.get('confirm_name') or '').strip()
        if typed != hospital.name:
            messages.error(request, "The name you typed does not match. Nothing was deleted.")
            return render(request, 'saas/hospital_confirm_delete.html',
                          {'hospital': hospital, 'stats': stats})

        name = hospital.name
        # Capture staff ids BEFORE the cascade nulls their hospital, and delete
        # them AFTER, when no tenant rows reference them any more.
        staff_ids = list(
            User.objects.filter(hospital=hospital, is_superuser=False)
            .values_list('id', flat=True))
        from audit.middleware import suppress_audit
        from saas.services import purge_tenant
        with transaction.atomic(), suppress_audit():
            purge_tenant(hospital)
            hospital.delete()
            if staff_ids:
                User.objects.filter(id__in=staff_ids).delete()

        messages.success(
            request,
            f"Hospital '{name}' and all of its data ({stats['patients']} patients, "
            f"{stats['staff']} staff, {stats['sales']} bills) were permanently deleted.")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_confirm_delete.html',
                  {'hospital': hospital, 'stats': stats})

@superuser_required
def payment_create(request):
    hospitals = Hospital.objects.all()
    if request.method == 'POST':
        hospital_id = request.POST.get('hospital_id')
        amount = request.POST.get('amount')
        payment_date = request.POST.get('payment_date')
        note = request.POST.get('note', '')

        hospital = get_object_or_404(Hospital, id=hospital_id)
        HospitalPayment.objects.create(
            hospital=hospital,
            amount=amount,
            payment_date=payment_date,
            note=note
        )
        messages.success(request, f"Recorded Rs {amount} payment for {hospital.name}!")
        return redirect('saas:dashboard')

    return render(request, 'saas/payment_form.html', {'hospitals': hospitals})

@superuser_required
def expense_create(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        amount = request.POST.get('amount')
        expense_date = request.POST.get('expense_date')
        note = request.POST.get('note', '')

        PlatformExpense.objects.create(
            title=title,
            amount=amount,
            expense_date=expense_date,
            note=note
        )
        messages.success(request, f"Recorded Rs {amount} platform expense for '{title}'!")
        return redirect('saas:dashboard')

    return render(request, 'saas/expense_form.html')


from django.contrib.auth import authenticate, login as auth_login
from django.utils import timezone

def render_hospital_login(request, hospital):
    """The tenant login page + POST handling for one hospital.

    Shared by the path route (`/<slug>/login/`) and the subdomain route
    (`<slug>.<BASE_DOMAIN>/login/`), so both behave identically: branded with the
    hospital's own name/logo/colour, and **isolated** — an account belonging to
    another hospital is rejected here even with a correct password (only that
    hospital's staff, or a superuser, may sign in on its portal).
    """
    from user_mgmt.models import SiteSettings

    # Check if subscription is active
    if not hospital.is_active or hospital.expiry_date < timezone.now().date():
        return render(request, 'saas/suspended.html', {'hospital': hospital})

    from saas.utils import set_current_hospital, clear_current_hospital
    set_current_hospital(hospital)
    try:
        branding = SiteSettings.load()
    finally:
        clear_current_hospital()

    error_message = None
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, email=email, password=password)
        if user is not None:
            # Verify the user belongs to this hospital (or is a superuser)
            if user.is_superuser or user.hospital == hospital:
                auth_login(request, user)
                return redirect('user_mgmt:post_login_redirect')
            else:
                error_message = f"This account does not belong to {hospital.name}."
        else:
            error_message = "Invalid email or password."

    return render(request, 'saas/login.html', {
        'hospital': hospital,
        'branding': branding,
        'error_message': error_message
    })


def hospital_login(request, hospital_slug):
    hospital = get_object_or_404(Hospital, slug=hospital_slug)
    return render_hospital_login(request, hospital)


def tenant_login_url(request, hospital):
    """Absolute login URL for a hospital — its subdomain when a real base domain
    is configured (`<slug>.<BASE_DOMAIN>/login/`), else the path form
    (`/<slug>/login/`) for dev / LAN / hosts without wildcard DNS."""
    from django.conf import settings
    base = (getattr(settings, 'BASE_DOMAIN', '') or '').lower()
    if base and '.' in base and base != 'localhost':
        scheme = 'https' if request.is_secure() else 'http'
        return f"{scheme}://{hospital.slug}.{base}/login/"
    return request.build_absolute_uri(f"/{hospital.slug}/login/")
