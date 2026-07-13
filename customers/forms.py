from django import forms
from .models import Customer, CustomerPayment


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["type", "name", "shop_name", "phone", "area", "credit_limit", "is_active"]


class CustomerPaymentForm(forms.ModelForm):
    class Meta:
        model = CustomerPayment
        fields = ["amount", "date", "method", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
