from django import forms

from .models import Panel, PanelPayment


class PanelForm(forms.ModelForm):
    # Rendered as checkboxes; stored on the JSONField `covered_services`. Leaving
    # every box unticked = no restriction (the panel covers everything).
    covered_services = forms.MultipleChoiceField(
        choices=Panel.SERVICE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Covered services",
        help_text="Tick only the services this card pays for. Leave all unticked "
                  "to cover everything.",
    )

    class Meta:
        model = Panel
        fields = ["name", "type", "contact_person", "phone", "address",
                  "copay_percent", "covered_services", "notes", "is_active"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
        }


class PanelPaymentForm(forms.ModelForm):
    class Meta:
        model = PanelPayment
        fields = ["amount", "date", "method", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
