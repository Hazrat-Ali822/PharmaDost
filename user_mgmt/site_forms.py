from django import forms
from .models import SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ["brand_name", "brand_tagline", "logo_text", "logo_image",
                  "primary_color", "accent_color",
                  "address", "phone", "email", "license_no", "receipt_footer",
                  "print_theme", "show_doctor_to_pharmacy"]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
        }
