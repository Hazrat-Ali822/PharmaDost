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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Reception should not have to invent a number — the next one in this
        # hospital's sequence is allocated on save. Kept editable so a hospital
        # migrating from a paper register can carry its existing MRNs across.
        self.fields['mrn'].required = False
        if self.instance.pk:
            self.fields['mrn'].help_text = 'Changing this rewrites the number on the patient card.'
        else:
            from .services import _settings_row, derive_prefix, MRN_DIGITS
            from saas.utils import get_current_hospital
            row = _settings_row(get_current_hospital())
            prefix = (row.mrn_prefix or derive_prefix(row.brand_name)).upper()
            nxt = f"{prefix}-{(row.mrn_last_number or 0) + 1:0{MRN_DIGITS}d}"
            self.fields['mrn'].widget.attrs['placeholder'] = nxt
            self.fields['mrn'].help_text = f'Leave blank to use the next number ({nxt}).'

    def clean_mrn(self):
        """Blank means auto-allocate. A typed one must be free in THIS hospital —
        the same number in another tenant is fine and expected."""
        mrn = (self.cleaned_data.get('mrn') or '').strip()
        if not mrn:
            # Clearing the box on an EDIT must not blank an issued number —
            # auto-allocation only ever happens on create.
            return self.instance.mrn if self.instance.pk else ''
        clash = Patient.objects.filter(mrn=mrn)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('Another patient here already has this MRN.')
        return mrn


class ClinicalRecordForm(forms.ModelForm):
    class Meta:
        model = ClinicalRecord
        fields = ['record_type', 'date', 'title', 'diagnosis', 'details',
                  'bp', 'pulse', 'temperature', 'weight']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'details': forms.Textarea(attrs={'rows': 4}),
        }
