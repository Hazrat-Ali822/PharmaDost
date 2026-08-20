from django import forms
from django.db.models import Q
from saas.forms import TenantModelForm
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import Referral


class ReferralForm(TenantModelForm):
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
        # Unconditional: `scoped_doctors` decides for itself what a superuser
        # or a hospital-less user may see. Guarding it with `if user is not
        # None` was the fail-open shape — a caller that forgot `user=` got
        # every tenant's doctors, and six forms carried that same copy.
        docs = scoped_doctors(user, Doctor.objects.filter(is_active=True))
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['referring_doctor'].queryset = docs
        self.fields['referring_doctor'].required = False
