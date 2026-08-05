from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required

from .forms import AntenatalVisitForm, DeliveryForm, PregnancyForm
from .models import Birth, Delivery, Pregnancy


@feature_required('maternity')
def maternity_list(request):
    pregnancies = Pregnancy.objects.select_related('mother').filter(status='ONGOING')
    return render(request, 'maternity/list.html', {'pregnancies': pregnancies})


@feature_required('maternity')
def pregnancy_register(request):
    if request.method == 'POST':
        form = PregnancyForm(request.POST, user=request.user)
        if form.is_valid():
            preg = form.save(commit=False)
            preg.registered_by = request.user
            preg.save()
            messages.success(request, 'ANC / pregnancy registered.')
            return redirect('maternity_pregnancy', pk=preg.pk)
    else:
        form = PregnancyForm(user=request.user)
    return render(request, 'maternity/pregnancy_form.html', {'form': form})


@feature_required('maternity')
def pregnancy_detail(request, pk):
    preg = get_object_or_404(Pregnancy.objects.select_related('mother'), pk=pk)
    if request.method == 'POST':
        form = AntenatalVisitForm(request.POST, user=request.user)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.pregnancy = preg
            visit.save()
            messages.success(request, 'Antenatal visit recorded.')
            return redirect('maternity_pregnancy', pk=preg.pk)
    else:
        form = AntenatalVisitForm(user=request.user, initial={'date': timezone.localdate()})
    return render(request, 'maternity/pregnancy_detail.html', {
        'preg': preg, 'form': form,
        'visits': preg.visits.select_related('seen_by').all(),
        'deliveries': preg.deliveries.all(),
    })


@feature_required('maternity')
def delivery_record(request, pregnancy_id=None):
    preg = None
    if pregnancy_id:
        preg = get_object_or_404(Pregnancy, pk=pregnancy_id)
    if request.method == 'POST':
        form = DeliveryForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                delivery = form.save(commit=False)
                delivery.pregnancy = preg
                delivery.created_by = request.user
                delivery.save()
                # Babies: parallel arrays; a row counts if a sex was chosen.
                sexes = request.POST.getlist('baby_sex[]')
                weights = request.POST.getlist('baby_weight[]')
                statuses = request.POST.getlist('baby_status[]')
                times = request.POST.getlist('baby_time[]')
                made = 0
                for i, sex in enumerate(sexes):
                    if not sex:
                        continue
                    weight = None
                    try:
                        raw = weights[i].strip() if i < len(weights) else ''
                        weight = Decimal(raw) if raw else None
                    except (InvalidOperation, IndexError):
                        weight = None
                    Birth.objects.create(
                        delivery=delivery, sex=sex, weight_kg=weight,
                        status=(statuses[i] if i < len(statuses) and statuses[i] else 'ALIVE'),
                        birth_time=(times[i] or None) if i < len(times) else None,
                    )
                    made += 1
                if made == 0 and delivery.outcome == 'LIVE':
                    Birth.objects.create(delivery=delivery, sex='M', status='ALIVE')

                fee = form.cleaned_data.get('consultation_fee')
                if fee:
                    from billing.services import create_service_invoice
                    label = dict(Delivery.TYPE_CHOICES).get(delivery.delivery_type, 'Delivery')
                    inv = create_service_invoice(
                        patient=delivery.mother, created_by=request.user,
                        items=[(f'Delivery — {label}', Decimal(str(fee)))])
                    if inv:
                        delivery.invoice = inv
                        delivery.save(update_fields=['invoice'])
                if preg:
                    preg.status = 'DELIVERED'
                    preg.save(update_fields=['status'])
            messages.success(request, 'Delivery recorded.')
            return redirect('maternity_birth_register')
    else:
        initial = {'delivered_at': timezone.now()}
        if preg:
            initial['mother'] = preg.mother_id
        form = DeliveryForm(user=request.user, initial=initial)
    return render(request, 'maternity/delivery_form.html', {
        'form': form, 'preg': preg,
        'sex_choices': Birth.SEX_CHOICES, 'status_choices': Birth.STATUS_CHOICES,
    })


@feature_required('maternity')
def birth_register(request):
    births = Birth.objects.select_related('delivery', 'delivery__mother', 'delivery__conducted_by')\
        .order_by('-delivery__delivered_at')[:300]
    return render(request, 'maternity/birth_register.html', {'births': births})
