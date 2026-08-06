from django import forms

from patients.models import Patient

from .models import Vaccine, VaccinationRecord


class VaccineForm(forms.ModelForm):
    class Meta:
        model = Vaccine
        fields = ['code', 'name', 'recommended_age', 'doses_in_series', 'sequence',
                  'description', 'is_active']


class VaccinationRecordForm(forms.ModelForm):
    class Meta:
        model = VaccinationRecord
        fields = ['patient', 'vaccine', 'dose_number', 'date_given', 'batch_no',
                  'next_due_date', 'given_by', 'notes']
        widgets = {
            'date_given': forms.DateInput(attrs={'type': 'date'}),
            'next_due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vaccine'].queryset = Vaccine.objects.filter(is_active=True)
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['patient'].queryset = pts.order_by('full_name')
