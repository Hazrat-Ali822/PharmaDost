from django import forms

from .models import Panel, PanelPayment


class PanelForm(forms.ModelForm):
    class Meta:
        model = Panel
        fields = ["name", "type", "contact_person", "phone", "address",
                  "copay_percent", "notes", "is_active"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
        }


class PanelPaymentForm(forms.ModelForm):
    class Meta:
        model = PanelPayment
        fields = ["amount", "date", "method", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
