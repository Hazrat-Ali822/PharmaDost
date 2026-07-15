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

@superuser_required
def saas_dashboard(request):
    hospitals = Hospital.objects.all().order_by('-created_at')
    payments = HospitalPayment.objects.all().order_by('-payment_date')[:10]
    expenses = PlatformExpense.objects.all().order_by('-expense_date')[:10]

    # Metrics
    active_count = Hospital.objects.filter(is_active=True).count()
    projected_income = Hospital.objects.filter(is_active=True).aggregate(total=models.Sum('monthly_price'))['total'] or 0
    total_received = HospitalPayment.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    total_expense = PlatformExpense.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    net_profit = total_received - total_expense

    context = {
        'hospitals': hospitals,
        'payments': payments,
        'expenses': expenses,
        'active_count': active_count,
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

def hospital_login(request, hospital_slug):
    from user_mgmt.models import SiteSettings
    hospital = get_object_or_404(Hospital, slug=hospital_slug)
    
    # Check if subscription is active
    if not hospital.is_active or hospital.expiry_date < timezone.now().date():
        return render(request, 'saas/suspended.html', {'hospital': hospital})

    branding, _ = SiteSettings.objects.get_or_create(hospital=hospital)

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
