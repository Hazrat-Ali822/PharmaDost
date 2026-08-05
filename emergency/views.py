from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import feature_required
from patients.models import Patient

from .forms import DispositionForm, EmergencyIntakeForm
from .models import EmergencyCase
from .services import register_case


def _scoped_cases(request):
    qs = EmergencyCase.objects.select_related('patient', 'attending_doctor')
    return qs  # TenantManager already scopes by hospital


@feature_required('emergency')
def emergency_board(request):
    """The live casualty board — open cases, sickest (RED) first."""
    open_cases = list(_scoped_cases(request).filter(
        disposition__in=EmergencyCase.ACTIVE_DISPOSITIONS))
    open_cases.sort(key=lambda c: (c.triage_rank, c.arrival_time))
    counts = {key: 0 for key, _ in EmergencyCase.TRIAGE_CHOICES}
    for c in open_cases:
        counts[c.triage] = counts.get(c.triage, 0) + 1
    today = timezone.localdate()
    recent = _scoped_cases(request).filter(
        created_at__date=today).exclude(disposition__in=EmergencyCase.ACTIVE_DISPOSITIONS)[:20]
    return render(request, 'emergency/board.html', {
        'open_cases': open_cases, 'counts': counts, 'recent': recent,
    })


@feature_required('emergency')
def emergency_intake(request):
    if request.method == 'POST':
        form = EmergencyIntakeForm(request.POST, user=request.user)
        if form.is_valid():
            cd = form.cleaned_data
            patient = cd.get('existing_patient')
            if not patient:
                patient = Patient.objects.create(
                    full_name=cd['new_name'].strip(),
                    gender=cd.get('new_gender') or '',
                    age_years=cd.get('new_age'),
                    phone=(cd.get('new_phone') or '').strip(),
                )
            case = register_case(
                patient=patient, created_by=request.user,
                triage=cd['triage'], chief_complaint=cd['chief_complaint'],
                mode_of_arrival=cd['mode_of_arrival'], brought_by=cd['brought_by'],
                is_mlc=cd['is_mlc'], mlc_no=cd['mlc_no'],
                pulse=cd['pulse'], bp=cd['bp'], temp=cd['temp'], spo2=cd['spo2'],
                attending_doctor=cd.get('attending_doctor'),
                consultation_fee=cd.get('consultation_fee'),
            )
            messages.success(request, f'Casualty case #{case.pk} registered for {patient.full_name}.')
            return redirect('emergency_board')
    else:
        form = EmergencyIntakeForm(user=request.user)
    return render(request, 'emergency/intake.html', {'form': form})


@feature_required('emergency')
def emergency_detail(request, pk):
    case = get_object_or_404(_scoped_cases(request), pk=pk)
    if request.method == 'POST':
        form = DispositionForm(request.POST, instance=case)
        if form.is_valid():
            case = form.save(commit=False)
            if case.disposition not in EmergencyCase.ACTIVE_DISPOSITIONS and not case.disposed_at:
                case.disposed_at = timezone.now()
            if case.disposition in EmergencyCase.ACTIVE_DISPOSITIONS:
                case.disposed_at = None
            case.save()
            messages.success(request, 'Case updated.')
            return redirect('emergency_detail', pk=case.pk)
    else:
        form = DispositionForm(instance=case)
    return render(request, 'emergency/detail.html', {'case': case, 'form': form})
