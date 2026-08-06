from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required

from .forms import ReferralForm
from .models import Referral


@feature_required('referral')
def referral_list(request):
    q = (request.GET.get('q') or '').strip()
    refs = Referral.objects.select_related('patient', 'referring_doctor')
    if q:
        refs = refs.filter(Q(patient__full_name__icontains=q) | Q(facility__icontains=q) |
                           Q(reason__icontains=q))
    return render(request, 'referral/list.html', {'referrals': refs[:200], 'q': q})


@feature_required('referral')
def referral_create(request):
    if request.method == 'POST':
        form = ReferralForm(request.POST, user=request.user)
        if form.is_valid():
            ref = form.save(commit=False)
            ref.created_by = request.user
            ref.save()
            messages.success(request, 'Referral saved.')
            return redirect('referral_detail', pk=ref.pk)
    else:
        form = ReferralForm(user=request.user)
    return render(request, 'referral/form.html', {'form': form})


@feature_required('referral')
def referral_detail(request, pk):
    ref = get_object_or_404(Referral.objects.select_related('patient', 'referring_doctor'), pk=pk)
    return render(request, 'referral/detail.html', {'ref': ref})


@feature_required('referral')
def referral_letter(request, pk):
    ref = get_object_or_404(Referral.objects.select_related('patient', 'referring_doctor'), pk=pk)
    return render(request, 'referral/letter.html', {'ref': ref})
