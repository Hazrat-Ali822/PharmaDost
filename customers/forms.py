from django import forms
from saas.forms import TenantModelForm
from .models import Customer, CustomerPayment


class CustomerForm(TenantModelForm):
    class Meta:
        model = Customer
        fields = ["type", "name", "shop_name", "phone", "area", "credit_limit", "is_active"]


class CustomerPaymentForm(TenantModelForm):
    class Meta:
        model = CustomerPayment
        fields = ["amount", "date", "method", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
