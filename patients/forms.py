import calendar
from datetime import date, timedelta

from django import forms
from .models import Patient, PatientDocument
from opd.models import ClinicalRecord


class PatientForm(forms.ModelForm):
    # Form-only. A paediatric patient's age is months and days, not a rounded
    # year, so reception needs somewhere to put them — but storing three numbers
    # alongside `dob` would give us two answers to the same question. They fold
    # into the date of birth instead (see `clean`).
    age_months = forms.IntegerField(required=False, min_value=0, max_value=11,
                                    label='Months')
    age_days = forms.IntegerField(required=False, min_value=0, max_value=31,
                                  label='Days')

    class Meta:
        model = Patient
        fields = [
            'mrn', 'full_name', 'guardian_name', 'cnic', 'phone',
            'dob', 'age_years', 'gender', 'address', 'blood_group', 'allergies',
            'panel', 'panel_member_id', 'panel_coverage_limit',
        ]
        # Declared fields (age_months/age_days) otherwise land at the very bottom
        # of the form, far from the Years box they belong with.
        field_order = [
            'mrn', 'full_name', 'guardian_name', 'cnic', 'phone',
            'dob', 'age_years', 'age_months', 'age_days',
            'gender', 'blood_group', 'address', 'allergies',
            'panel', 'panel_member_id', 'panel_coverage_limit',
        ]
        widgets = {
            # Typed as DD/MM/YYYY rather than a native <input type="date">: that
            # renders in the BROWSER's locale, so the same record reads
            # 29/01/2002 on one desk and 01/29/2002 on another. A date of birth
            # that can be misread by half the staff is not worth the free picker
            # — the calendar button next to it gives that back.
            'dob': forms.DateInput(format='%d/%m/%Y', attrs={
                'placeholder': 'DD/MM/YYYY',
                'inputmode': 'numeric',
                'maxlength': '10',
                'autocomplete': 'off',
                'class': 'dob-input',
            }),
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
        self.fields['age_years'].label = 'Years'
        self.fields['dob'].label = 'Date of birth'
        # Accept what the box shows first, but keep the ISO form working so the
        # calendar picker, an import or an API caller all parse.
        self.fields['dob'].input_formats = ['%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d']
        for name in ('age_years', 'age_months', 'age_days'):
            self.fields[name].widget.attrs.setdefault('min', 0)
        if self.instance.pk and self.instance.dob:
            years, months, days = self.instance.age_parts
            self.initial.setdefault('age_years', years)
            self.initial.setdefault('age_months', months)
            self.initial.setdefault('age_days', days)
        # The MRN is issued by the system and shown, never typed. Reception must
        # not be able to edit it — a hand-changed number breaks the link to every
        # bill, report and card already printed with it.
        # Panel cover is optional everywhere (registration, reception, offline
        # replay). The coverage limit especially must not be required — offline
        # patient/visit payloads never carry it. clean_panel_coverage_limit coerces
        # a blank to 0 so the non-null column is satisfied.
        for name in ('panel', 'panel_member_id', 'panel_coverage_limit'):
            if name in self.fields:
                self.fields[name].required = False
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

    def clean_panel_coverage_limit(self):
        from decimal import Decimal
        return self.cleaned_data.get('panel_coverage_limit') or Decimal('0.00')

    def clean_mrn(self):
        """`disabled=True` already makes Django ignore whatever is posted and fall
        back to the initial value. On create that initial is only a preview, so
        return blank and let `Patient.save()` reserve the real number — two
        receptionists with the form open would otherwise both hold the same one."""
        return self.instance.mrn if self.instance.pk else ''

    def clean(self):
        """Fold a months/days age into a date of birth.

        'Only years' stays a suggestion the form makes and the user can see — a
        bare '35' does not tell us the day, and writing one down would put a
        precise-looking guess on a medical record. But once months or days are
        given the entry IS day-precise, so deriving the date is arithmetic on
        what reception actually said, not an invention.
        """
        cleaned = super().clean()
        if cleaned.get('dob'):
            return cleaned                      # a real date always wins

        months = cleaned.get('age_months') or 0
        days = cleaned.get('age_days') or 0
        if not months and not days:
            return cleaned

        from django.utils import timezone
        today = timezone.localdate()
        years = cleaned.get('age_years') or 0
        total_months = years * 12 + months
        year, month = divmod(today.month - 1 - total_months, 12)
        year, month = today.year + year, month + 1
        day = min(today.day, calendar.monthrange(year, month)[1])
        cleaned['dob'] = date(year, month, day) - timedelta(days=days)
        self.instance.dob = cleaned['dob']
        return cleaned

    def clean_cnic(self):
        """Store one shape: 13 digits as XXXXX-XXXXXXX-X. Search only matches when
        everyone types it the same way, so normalise here rather than trusting the
        browser — an import or a JS-off browser must land in the same format."""
        raw = (self.cleaned_data.get('cnic') or '').strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if not digits:
            # Blank is fine — a CNIC is optional. Something that is not blank and
            # yet holds no digit at all is a typo in the wrong box, and silently
            # throwing it away told the user their entry had been accepted.
            if raw:
                raise forms.ValidationError(
                    'That does not look like a CNIC — it has no digits in it.')
            return ''
        if len(digits) != 13:
            raise forms.ValidationError(
                f'A CNIC has 13 digits — you entered {len(digits)}.')
        return f'{digits[:5]}-{digits[5:12]}-{digits[12]}'

    def clean_phone(self):
        """A phone number is how the hospital reaches the patient, and it had no
        validation at all: `not-a-phone` saved and showed in the registry exactly
        like that. Reminders then fail silently against it for ever.

        Normalised to one shape for the same reason the CNIC is — a Pakistani
        mobile is stored `03XX-XXXXXXX`, so the reception search has something
        predictable to match. Anything else (a landline, an international number)
        is kept as the user typed it once it clears the digit check, because this
        market has too many shapes to enumerate and refusing a real number is
        worse than storing an unusual one.
        """
        raw = (self.cleaned_data.get('phone') or '').strip()
        if not raw:
            return ''
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) < 7:
            raise forms.ValidationError(
                'That does not look like a phone number. Enter it as '
                '03XX-XXXXXXX.')
        if len(digits) == 11 and digits.startswith('03'):
            return f'{digits[:4]}-{digits[4:]}'
        return raw


class ClinicalRecordForm(forms.ModelForm):
    class Meta:
        model = ClinicalRecord
        fields = ['record_type', 'date', 'title', 'diagnosis', 'details',
                  'bp', 'pulse', 'temperature', 'weight']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'details': forms.Textarea(attrs={'rows': 4}),
        }


class PatientDocumentForm(forms.ModelForm):
    """Upload a photograph of a paper document.

    `capture="environment"` opens the phone's rear camera straight from the
    button, which is the whole workflow: point at the sheet, shoot, done. On a
    desktop it degrades to an ordinary file picker.
    """

    class Meta:
        model = PatientDocument
        fields = ['image', 'kind', 'title', 'doc_date', 'note']
        widgets = {
            'image': forms.ClearableFileInput(attrs={
                'accept': 'image/*', 'capture': 'environment'}),
            'doc_date': forms.DateInput(attrs={'type': 'date'}),
            'title': forms.TextInput(attrs={'placeholder': 'Optional — "Dr. Sara, BP medicines"'}),
            'note': forms.TextInput(attrs={'placeholder': 'optional'}),
        }

    def clean_image(self):
        from .images import MAX_UPLOAD_BYTES

        image = self.cleaned_data.get('image')
        # Only a fresh upload has `.size`; leaving an existing file alone does not.
        size = getattr(image, 'size', 0)
        if size and size > MAX_UPLOAD_BYTES:
            mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(
                f'That picture is {size / 1024 / 1024:.0f} MB — the limit is {mb} MB. '
                f'Photograph it again at a lower resolution.')
        return image
