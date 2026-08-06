from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required

from .forms import BloodDonorForm, BloodIssueForm, BloodUnitForm
from .models import BLOOD_GROUPS, BloodDonor, BloodIssue, BloodUnit


@feature_required('bloodbank')
def bloodbank_dashboard(request):
    today = timezone.localdate()
    units = BloodUnit.objects.filter(status='AVAILABLE', expiry_date__gte=today)
    # available count per blood group (no query per group)
    counts = {g: 0 for g, _ in BLOOD_GROUPS}
    for u in units.values('blood_group'):
        counts[u['blood_group']] = counts.get(u['blood_group'], 0) + 1
    stock = [{'group': g, 'count': counts.get(g, 0)} for g, _ in BLOOD_GROUPS]
    expiring = BloodUnit.objects.filter(status='AVAILABLE', expiry_date__gte=today,
                                        expiry_date__lte=today + timedelta(days=7)).order_by('expiry_date')
    ctx = {
        'stock': stock,
        'total_available': units.count(),
        'donor_count': BloodDonor.objects.count(),
        'expiring': expiring,
    }
    return render(request, 'bloodbank/dashboard.html', ctx)


@feature_required('bloodbank')
def donor_list(request):
    donors = BloodDonor.objects.all()
    g = request.GET.get('group')
    if g:
        donors = donors.filter(blood_group=g)
    if request.method == 'POST':
        form = BloodDonorForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Donor registered.')
            return redirect('bloodbank_donors')
    else:
        form = BloodDonorForm()
    return render(request, 'bloodbank/donors.html',
                  {'donors': donors[:300], 'form': form, 'groups': BLOOD_GROUPS, 'group': g})


@feature_required('bloodbank')
def unit_list(request):
    units = BloodUnit.objects.select_related('donor')
    g = request.GET.get('group')
    st = request.GET.get('status')
    if g:
        units = units.filter(blood_group=g)
    if st:
        units = units.filter(status=st)
    return render(request, 'bloodbank/units.html',
                  {'units': units[:300], 'groups': BLOOD_GROUPS, 'group': g, 'status': st,
                   'statuses': BloodUnit.STATUS})


@feature_required('bloodbank')
def unit_add(request):
    if request.method == 'POST':
        form = BloodUnitForm(request.POST, user=request.user)
        if form.is_valid():
            unit = form.save(commit=False)
            unit.created_by = request.user
            unit.save()
            messages.success(request, f'Unit {unit.bag_number} added to stock.')
            return redirect('bloodbank_units')
    else:
        form = BloodUnitForm(user=request.user)
    return render(request, 'bloodbank/unit_form.html', {'form': form})


@feature_required('bloodbank')
def unit_discard(request, pk):
    unit = get_object_or_404(BloodUnit, pk=pk)
    if request.method == 'POST':
        unit.status = 'DISCARDED'
        unit.save(update_fields=['status'])
        messages.success(request, f'Unit {unit.bag_number} discarded.')
    return redirect('bloodbank_units')


@feature_required('bloodbank')
def issue_create(request):
    if request.method == 'POST':
        form = BloodIssueForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                unit = BloodUnit.objects.select_for_update().get(pk=form.cleaned_data['unit'].pk)
                if unit.status != 'AVAILABLE':
                    messages.error(request, 'That unit is no longer available.')
                    return redirect('bloodbank_issue')
                issue = form.save(commit=False)
                issue.issued_by = request.user
                issue.save()
                unit.status = 'ISSUED'
                unit.save(update_fields=['status'])
            messages.success(request, f'Unit {unit.bag_number} issued to {issue.patient.full_name}.')
            return redirect('bloodbank_dashboard')
    else:
        form = BloodIssueForm(user=request.user)
    recent = BloodIssue.objects.select_related('unit', 'patient')[:50]
    return render(request, 'bloodbank/issue_form.html', {'form': form, 'recent': recent})
