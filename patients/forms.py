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
            # 13 digits + 2 dashes. inputmode gets a phone's numeric keypad up;
            # the dashes are inserted as the user types (see patient_form.html).
            'cnic': forms.TextInput(attrs={
                'placeholder': '35202-1234567-1',
                'maxlength': '15',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The MRN is issued by the system and shown, never typed. Reception must
        # not be able to edit it — a hand-changed number breaks the link to every
        # bill, report and card already printed with it.
        self.fields['mrn'].required = False
        self.fields['mrn'].disabled = True
        self.fields['mrn'].label = 'MRN (auto)'
        if self.instance.pk:
            self.fields['mrn'].help_text = 'Issued by the system — cannot be changed.'
        else:
            from .services import _settings_row, derive_prefix, MRN_DIGITS
            from saas.utils import get_current_hospital
            row = _settings_row(get_current_hospital())
            prefix = (row.mrn_prefix or derive_prefix(row.brand_name)).upper()
            nxt = f"{prefix}-{(row.mrn_last_number or 0) + 1:0{MRN_DIGITS}d}"
            self.initial['mrn'] = nxt
            self.fields['mrn'].help_text = 'Allocated automatically when you save.'

    def clean_mrn(self):
        """`disabled=True` already makes Django ignore whatever is posted and fall
        back to the initial value. On create that initial is only a preview, so
        return blank and let `Patient.save()` reserve the real number — two
        receptionists with the form open would otherwise both hold the same one."""
        return self.instance.mrn if self.instance.pk else ''

    def clean_cnic(self):
        """Store one shape: 13 digits as XXXXX-XXXXXXX-X. Search only matches when
        everyone types it the same way, so normalise here rather than trusting the
        browser — an import or a JS-off browser must land in the same format."""
        raw = (self.cleaned_data.get('cnic') or '').strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if not digits:
            return ''
        if len(digits) != 13:
            raise forms.ValidationError(
                f'A CNIC has 13 digits — you entered {len(digits)}.')
        return f'{digits[:5]}-{digits[5:12]}-{digits[12]}'


class ClinicalRecordForm(forms.ModelForm):
    class Meta:
        model = ClinicalRecord
        fields = ['record_type', 'date', 'title', 'diagnosis', 'details',
                  'bp', 'pulse', 'temperature', 'weight']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'details': forms.Textarea(attrs={'rows': 4}),
        }
