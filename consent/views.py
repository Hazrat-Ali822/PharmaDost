import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import feature_required, role_required

from .forms import ConsentRecordForm, ConsentTemplateForm
from .models import ConsentForm, ConsentTemplate


@feature_required('consent')
def consent_list(request):
    records = ConsentForm.objects.select_related('patient', 'doctor')[:200]
    return render(request, 'consent/list.html', {'records': records})


@feature_required('consent')
def consent_create(request):
    if request.method == 'POST':
        form = ConsentRecordForm(request.POST, user=request.user)
        if form.is_valid():
            rec = form.save(commit=False)
            rec.created_by = request.user
            rec.save()
            messages.success(request, 'Consent recorded.')
            return redirect('consent_print', pk=rec.pk)
    else:
        form = ConsentRecordForm(user=request.user)
    # {template_id: {title, consent_type, body}} so the page can pre-fill on select
    tpl_map = {str(t.pk): {'title': t.title, 'consent_type': t.consent_type, 'body': t.body}
               for t in form.fields['template'].queryset}
    return render(request, 'consent/form.html', {'form': form, 'tpl_json': json.dumps(tpl_map)})


@feature_required('consent')
def consent_print(request, pk):
    rec = get_object_or_404(ConsentForm.objects.select_related('patient', 'doctor'), pk=pk)
    return render(request, 'consent/print.html', {'c': rec})


@feature_required('consent')
@role_required(['ADMIN'])
def template_list(request):
    templates = ConsentTemplate.objects.all()
    if request.method == 'POST':
        form = ConsentTemplateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Consent template saved.')
            return redirect('consent_templates')
    else:
        form = ConsentTemplateForm()
    return render(request, 'consent/templates.html', {'templates': templates, 'form': form})
