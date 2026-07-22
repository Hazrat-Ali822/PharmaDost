from django import forms
from .models import Ward, Bed, Admission, DoctorRound
from patients.models import Patient
from opd.models import Doctor

class WardForm(forms.ModelForm):
    class Meta:
        model = Ward
        fields = ['name', 'ward_type', 'daily_rate']

class BedForm(forms.ModelForm):
    class Meta:
        model = Bed
        fields = ['bed_number', 'ward', 'status']

class AdmissionForm(forms.ModelForm):
    class Meta:
        model = Admission
        fields = ['patient', 'bed', 'attending_doctor', 'admission_reason']
        widgets = {
            'admission_reason': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter patients, doctors, and beds by current hospital (TenantManager does it, but we can double check)
        self.fields['patient'].queryset = Patient.objects.all().order_by('full_name')
        self.fields['attending_doctor'].queryset = Doctor.objects.all().order_by('full_name')
        
        # Only show available beds, plus the currently assigned bed if editing
        avail_beds = Bed.objects.filter(status='Available')
        if self.instance and self.instance.pk and self.instance.bed:
            avail_beds = avail_beds | Bed.objects.filter(pk=self.instance.bed.pk)
        self.fields['bed'].queryset = avail_beds.distinct()

class DoctorRoundForm(forms.ModelForm):
    class Meta:
        model = DoctorRound
        fields = ['vitals_temp', 'vitals_bp', 'vitals_pulse', 'clinical_notes', 'prescription_updates']
        widgets = {
            'clinical_notes': forms.Textarea(attrs={'rows': 3}),
            'prescription_updates': forms.Textarea(attrs={'rows': 2}),
        }

from .models import Ward, Bed, Admission, DoctorRound, MedicationLog

class DischargeForm(forms.ModelForm):
    class Meta:
        model = Admission
        fields = ['discharge_notes']
        widgets = {
            'discharge_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Enter discharge summary and medications advised...'}),
        }

class MedicationLogForm(forms.ModelForm):
    class Meta:
        model = MedicationLog
        fields = ['medicine_name', 'dosage', 'administered_at', 'notes']
        widgets = {
            # Backed by a <datalist> of the pharmacy's catalogue (see the template)
            # so ward staff can search instead of typing a drug name from memory.
            # Deliberately still free text: a ward may administer something the
            # pharmacy does not stock, and that must remain recordable.
            'medicine_name': forms.TextInput(attrs={
                'list': 'pharmacy-medicines',
                'autocomplete': 'off',
                'placeholder': 'Start typing to search the pharmacy…',
            }),
            'administered_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.TextInput(attrs={'placeholder': 'e.g. given after lunch, patient tolerated well'}),
        }
