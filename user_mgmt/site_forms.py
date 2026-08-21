from django import forms
from .models import SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ["brand_name", "brand_tagline", "logo_text", "logo_image",
                  "primary_color", "accent_color", "default_theme",
                  "address", "phone", "email", "license_no", "receipt_footer",
                  "print_theme", "currency_symbol",
                  "show_doctor_to_pharmacy", "whatsapp_enabled",
                  "show_bill_qr", "mrn_prefix", "mrn_last_number",
                  "invoice_prefix", "invoice_year_in_number", "invoice_last_number",
                  "default_tax_percent", "default_discount_percent", "bill_rounding"]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "mrn_prefix": forms.TextInput(attrs={"placeholder": "e.g. SGH",
                                                 "style": "text-transform:uppercase"}),
        }

    # Fields that have a sensible default: a blank box (or an omitted field on a
    # partial post) falls back to the default rather than failing validation.
    OPTIONAL_DEFAULTS = {
        "currency_symbol": "Rs",
        "invoice_prefix": "INV",
        "invoice_last_number": 0,
        "default_tax_percent": 0,
        "default_discount_percent": 0,
        "bill_rounding": "none",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from patients.services import derive_prefix
        if not (self.instance.mrn_prefix or ''):
            self.fields['mrn_prefix'].widget.attrs['placeholder'] = derive_prefix(
                self.instance.brand_name)
        for name in self.OPTIONAL_DEFAULTS:
            if name in self.fields:
                self.fields[name].required = False

    def clean_mrn_prefix(self):
        val = (self.cleaned_data.get('mrn_prefix') or '').strip().upper()
        if val:
            # Check if another hospital is already using this exact mrn_prefix
            qs = SiteSettings.objects.filter(mrn_prefix__iexact=val)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(f"The MRN prefix '{val}' is already in use by another hospital. Please choose a unique prefix.")
        return val

    def clean(self):
        cleaned = super().clean()
        for name, default in self.OPTIONAL_DEFAULTS.items():
            if cleaned.get(name) in (None, ''):
                cleaned[name] = default
        return cleaned

    def clean_logo_image(self):
        """Refuse an enormous file, and shrink everything else.

        The logo is fetched on every page of every visit, so an unshrunk phone
        photograph is paid for over and over — the public demo's logo was a 1 MB
        photograph of a pair of trainers until this existed.

        Only an actual upload is touched. Leaving the field alone hands back the
        existing `FieldFile`, and re-processing that would rewrite a stored logo
        every time an unrelated setting is saved.
        """
        from django.core.files.uploadedfile import UploadedFile
        from .branding_images import MAX_LOGO_BYTES, compress_logo

        value = self.cleaned_data.get('logo_image')
        if not isinstance(value, UploadedFile):
            return value
        if value.size > MAX_LOGO_BYTES:
            raise forms.ValidationError(
                'That picture is %.1f MB. A logo should be under %d MB — '
                'this looks like a photograph rather than a logo.'
                % (value.size / 1048576.0, MAX_LOGO_BYTES // 1048576))
        return compress_logo(value)

    def clean_mrn_prefix(self):
        """Uppercase letters/digits only — it is printed on the patient card and
        typed at the counter, so a space or a slash there is a support call."""
        import re
        value = (self.cleaned_data.get('mrn_prefix') or '').strip().upper()
        if value and not re.fullmatch(r'[A-Z0-9]{1,6}', value):
            raise forms.ValidationError('Use 1–6 letters or digits, e.g. SGH.')
        return value
