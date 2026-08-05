from django import forms
from django.db.models import Q

from opd.models import Doctor
from patients.models import Patient

from .models import EmergencyCase


class EmergencyIntakeForm(forms.ModelForm):
    """Triage-first intake. Either pick a registered patient or quick-add a new
    one (name is enough — the emergency room registers first, completes later)."""
    existing_patient = forms.ModelChoiceField(
        queryset=Patient.objects.all(), required=False, label='Existing patient')
    new_name = forms.CharField(required=False, label='New patient name')
    new_gender = forms.ChoiceField(required=False, label='Gender',
                                   choices=[('', '—'), ('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    new_age = forms.IntegerField(required=False, min_value=0, label='Age (years)')
    new_phone = forms.CharField(required=False, label='Phone')
    consultation_fee = forms.DecimalField(required=False, min_value=0,
                                          label='Emergency consultation fee (optional)')

    class Meta:
        model = EmergencyCase
        fields = ['triage', 'chief_complaint', 'mode_of_arrival', 'brought_by',
                  'is_mlc', 'mlc_no', 'pulse', 'bp', 'temp', 'spo2', 'attending_doctor']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        docs = Doctor.objects.filter(is_active=True)
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            docs = docs.filter(Q(user__hospital=user.hospital) | Q(user__isnull=True))
            pts = pts.filter(hospital=user.hospital)
        self.fields['attending_doctor'].queryset = docs
        self.fields['attending_doctor'].required = False
        self.fields['existing_patient'].queryset = pts

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('existing_patient') and not (cleaned.get('new_name') or '').strip():
            raise forms.ValidationError('Choose a registered patient or enter a new patient name.')
        return cleaned


class DispositionForm(forms.ModelForm):
    class Meta:
        model = EmergencyCase
        fields = ['disposition', 'disposition_notes', 'attending_doctor',
                  'pulse', 'bp', 'temp', 'spo2']
