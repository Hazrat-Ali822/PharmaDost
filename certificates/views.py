from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required

from .forms import BirthCertificateForm, DeathCertificateForm
from .models import BirthCertificate, DeathCertificate


@feature_required('certificates')
def certificate_list(request):
    births = BirthCertificate.objects.all()[:100]
    deaths = DeathCertificate.objects.select_related('patient', 'attending_doctor')[:100]
    return render(request, 'certificates/list.html', {'births': births, 'deaths': deaths})


@feature_required('certificates')
def birth_create(request):
    if request.method == 'POST':
        form = BirthCertificateForm(request.POST)
        if form.is_valid():
            cert = form.save(commit=False)
            cert.registered_by = request.user
            cert.save()
            messages.success(request, f'Birth certificate {cert.serial_no} issued.')
            return redirect('birth_certificate', pk=cert.pk)
    else:
        form = BirthCertificateForm()
    return render(request, 'certificates/birth_form.html', {'form': form})


@feature_required('certificates')
def death_create(request):
    if request.method == 'POST':
        form = DeathCertificateForm(request.POST, user=request.user)
        if form.is_valid():
            cert = form.save(commit=False)
            cert.registered_by = request.user
            cert.save()
            messages.success(request, f'Death certificate {cert.serial_no} issued.')
            return redirect('death_certificate', pk=cert.pk)
    else:
        form = DeathCertificateForm(user=request.user)
    return render(request, 'certificates/death_form.html', {'form': form})


@feature_required('certificates')
def birth_certificate(request, pk):
    cert = get_object_or_404(BirthCertificate, pk=pk)
    return render(request, 'certificates/birth_print.html', {'c': cert})


@feature_required('certificates')
def death_certificate(request, pk):
    cert = get_object_or_404(DeathCertificate.objects.select_related('patient', 'attending_doctor'), pk=pk)
    return render(request, 'certificates/death_print.html', {'c': cert})
