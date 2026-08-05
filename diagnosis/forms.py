from django import forms
from django.db.models import Q

from opd.models import Doctor
from patients.models import Patient

from .models import DiagnosisCode, PatientDiagnosis


class DiagnosisCodeForm(forms.ModelForm):
    class Meta:
        model = DiagnosisCode
        fields = ['code', 'title', 'category', 'is_active']


class PatientDiagnosisForm(forms.ModelForm):
    class Meta:
        model = PatientDiagnosis
        fields = ['patient', 'code', 'clinical_note', 'diagnosed_on', 'doctor']
        widgets = {'diagnosed_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['code'].queryset = DiagnosisCode.objects.filter(is_active=True)
        pts = Patient.objects.all()
        docs = Doctor.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
            docs = docs.filter(Q(user__hospital=user.hospital) | Q(user__isnull=True))
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['doctor'].queryset = docs
        self.fields['doctor'].required = False
