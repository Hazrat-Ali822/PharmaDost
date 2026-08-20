from django import forms
from saas.forms import TenantModelForm
from pharma_mgmt.widgets import DateInput
from .models import Supplier, SupplierPayment


class SupplierForm(TenantModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'address']


class SupplierPaymentForm(TenantModelForm):
    class Meta:
        model = SupplierPayment
        fields = ['amount', 'date', 'method', 'notes']
        widgets = {'date': DateInput()}
