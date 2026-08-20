from django import forms
from django.db.models import Q
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import Referral


class ReferralForm(forms.ModelForm):
    class Meta:
        model = Referral
        fields = ['patient', 'direction', 'facility', 'department', 'referring_doctor',
                  'receiving_doctor', 'reason', 'urgency', 'clinical_summary',
                  'investigations', 'treatment_given', 'referral_date', 'status']
        widgets = {
            'referral_date': DateInput(),
            'clinical_summary': forms.Textarea(attrs={'rows': 3}),
            'investigations': forms.Textarea(attrs={'rows': 2}),
            'treatment_given': forms.Textarea(attrs={'rows': 2}),
            'reason': forms.TextInput(attrs={'placeholder': 'e.g. needs CT + neurosurgery opinion'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        pts = Patient.objects.all()
        docs = Doctor.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
            docs = scoped_doctors(user, docs)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['referring_doctor'].queryset = docs
        self.fields['referring_doctor'].required = False
