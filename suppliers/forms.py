from django import forms
from .models import Supplier, SupplierPayment


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'address']


class SupplierPaymentForm(forms.ModelForm):
    class Meta:
        model = SupplierPayment
        fields = ['amount', 'date', 'method', 'notes']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}
