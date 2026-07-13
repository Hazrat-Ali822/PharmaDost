from django import forms
from .models import SurgeryCategory, SurgeryProcedure, SurgeryRecord
from patients.models import Patient
from ipd.models import Admission
from opd.models import Doctor

class SurgeryCategoryForm(forms.ModelForm):
    class Meta:
        model = SurgeryCategory
        fields = ['name']

class SurgeryProcedureForm(forms.ModelForm):
    class Meta:
        model = SurgeryProcedure
        fields = ['name', 'category', 'standard_charge']

class SurgeryRecordForm(forms.ModelForm):
    class Meta:
        model = SurgeryRecord
        fields = [
            'patient', 'admission', 'procedure', 'start_time', 'end_time',
            'lead_surgeon', 'surgical_team', 'anesthesia_type', 'operation_notes', 'outcome'
        ]
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'surgical_team': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Assistant surgeons, anesthetist, nurses names...'}),
            'operation_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Detailed surgical procedure findings...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter patients, active admissions, procedures, and doctors by current tenant
        self.fields['patient'].queryset = Patient.objects.all().order_by('full_name')
        self.fields['admission'].queryset = Admission.objects.filter(status='Admitted').order_by('-admission_date')
        self.fields['procedure'].queryset = SurgeryProcedure.objects.all().order_by('name')
        self.fields['lead_surgeon'].queryset = Doctor.objects.all().order_by('name')
