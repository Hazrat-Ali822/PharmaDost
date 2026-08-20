from django import forms
from django.db.models import Q
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import ConsentForm, ConsentTemplate


class ConsentTemplateForm(forms.ModelForm):
    class Meta:
        model = ConsentTemplate
        fields = ['title', 'consent_type', 'body', 'is_active']
        widgets = {'body': forms.Textarea(attrs={'rows': 6})}


class ConsentRecordForm(forms.ModelForm):
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
        docs = Doctor.objects.filter(is_active=True)
        tpls = ConsentTemplate.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
            docs = scoped_doctors(user, docs)
            tpls = tpls.filter(hospital=user.hospital)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['doctor'].queryset = docs
        self.fields['doctor'].required = False
        self.fields['template'].queryset = tpls
