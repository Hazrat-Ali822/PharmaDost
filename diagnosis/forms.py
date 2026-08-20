from django import forms
from django.db.models import Q
from saas.forms import TenantModelForm
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import DiagnosisCode, PatientDiagnosis


class DiagnosisCodeForm(TenantModelForm):
    class Meta:
        model = DiagnosisCode
        fields = ['code', 'title', 'category', 'is_active']


class PatientDiagnosisForm(TenantModelForm):
    class Meta:
        model = PatientDiagnosis
        fields = ['patient', 'code', 'clinical_note', 'diagnosed_on', 'doctor']
        widgets = {'diagnosed_on': DateInput()}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['code'].queryset = DiagnosisCode.objects.filter(is_active=True)
        # Unconditional: `scoped_doctors` decides for itself what a superuser
        # or a hospital-less user may see. Guarding it with `if user is not
        # None` was the fail-open shape — a caller that forgot `user=` got
        # every tenant's doctors, and six forms carried that same copy.
        docs = scoped_doctors(user, Doctor.objects.filter(is_active=True))
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['doctor'].queryset = docs
        self.fields['doctor'].required = False
