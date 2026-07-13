from django import forms
from .models import Patient
from opd.models import ClinicalRecord


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            'mrn', 'full_name', 'guardian_name', 'cnic', 'phone',
            'dob', 'age_years', 'gender', 'address', 'blood_group', 'allergies'
        ]
        widgets = {
            'dob': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 3}),
            'allergies': forms.Textarea(attrs={'rows': 2}),
        }


class ClinicalRecordForm(forms.ModelForm):
    class Meta:
        model = ClinicalRecord
        fields = ['record_type', 'date', 'title', 'diagnosis', 'details',
                  'bp', 'pulse', 'temperature', 'weight']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'details': forms.Textarea(attrs={'rows': 4}),
        }
