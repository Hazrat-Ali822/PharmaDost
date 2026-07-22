from django import forms
from .models import SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ["brand_name", "brand_tagline", "logo_text", "logo_image",
                  "primary_color", "accent_color",
                  "address", "phone", "email", "license_no", "receipt_footer",
                  "print_theme", "show_doctor_to_pharmacy",
                  "mrn_prefix", "mrn_last_number"]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "mrn_prefix": forms.TextInput(attrs={"placeholder": "e.g. SGH",
                                                 "style": "text-transform:uppercase"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from patients.services import derive_prefix
        if not (self.instance.mrn_prefix or ''):
            self.fields['mrn_prefix'].widget.attrs['placeholder'] = derive_prefix(
                self.instance.brand_name)

    def clean_mrn_prefix(self):
        """Uppercase letters/digits only — it is printed on the patient card and
        typed at the counter, so a space or a slash there is a support call."""
        import re
        value = (self.cleaned_data.get('mrn_prefix') or '').strip().upper()
        if value and not re.fullmatch(r'[A-Z0-9]{1,6}', value):
            raise forms.ValidationError('Use 1–6 letters or digits, e.g. SGH.')
        return value
