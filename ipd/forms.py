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
    """`medicine` is filled in by the search box (hidden field) when the nurse picks
    a catalogue item; leaving it empty records an off-catalogue drug with no stock
    movement and no charge."""

    class Meta:
        model = MedicationLog
        fields = ['medicine', 'medicine_name', 'dosage', 'quantity',
                  'administered_at', 'notes']
        widgets = {
            'medicine': forms.HiddenInput(),
            # Backed by a <datalist> of the pharmacy's catalogue (see the template)
            # so ward staff can search instead of typing a drug name from memory.
            # Deliberately still free text: a ward may administer something the
            # pharmacy does not stock, and that must remain recordable.
            'medicine_name': forms.TextInput(attrs={
                'list': 'pharmacy-medicines',
                'autocomplete': 'off',
                'placeholder': 'Start typing to search the pharmacy…',
            }),
            'quantity': forms.NumberInput(attrs={'min': 1, 'step': 1}),
            'administered_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.TextInput(attrs={'placeholder': 'e.g. given after lunch, patient tolerated well'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Medicine
        # Tenant-scoped by Medicine's manager; also validates the posted id really
        # belongs to this hospital.
        self.fields['medicine'].queryset = Medicine.objects.filter(is_active=True)
        self.fields['medicine'].required = False
        self.fields['quantity'].label = 'Quantity given (from stock)'

    def clean_quantity(self):
        qty = self.cleaned_data.get('quantity') or 1
        if qty < 1:
            raise forms.ValidationError('Quantity must be at least 1.')
        return qty
