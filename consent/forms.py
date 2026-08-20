from django import forms
from django.db.models import Q
from saas.forms import TenantModelForm
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import ConsentForm, ConsentTemplate


class ConsentTemplateForm(TenantModelForm):
    class Meta:
        model = ConsentTemplate
        fields = ['title', 'consent_type', 'body', 'is_active']
        widgets = {'body': forms.Textarea(attrs={'rows': 6})}


class ConsentRecordForm(TenantModelForm):
    template = forms.ModelChoiceField(queryset=ConsentTemplate.objects.none(), required=False,
                                      help_text='Pick to pre-fill the wording below')

    class Meta:
        model = ConsentForm
        fields = ['patient', 'template', 'consent_type', 'title', 'procedure_name',
                  'doctor', 'body', 'signed_by', 'relation', 'witness_name', 'signed_on']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 6}),
            'signed_on': DateInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        pts = Patient.objects.all()
        # Unconditional: `scoped_doctors` decides for itself what a superuser
        # or a hospital-less user may see. Guarding it with `if user is not
        # None` was the fail-open shape — a caller that forgot `user=` got
        # every tenant's doctors, and six forms carried that same copy.
        docs = scoped_doctors(user, Doctor.objects.filter(is_active=True))
        tpls = ConsentTemplate.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
            tpls = tpls.filter(hospital=user.hospital)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['doctor'].queryset = docs
        self.fields['doctor'].required = False
        self.fields['template'].queryset = tpls
