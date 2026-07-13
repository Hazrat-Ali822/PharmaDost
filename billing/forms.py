from django import forms
from .models import Invoice, Expense, CashClosing


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['patient', 'appointment', 'payment_method', 'discount', 'paid']


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'category', 'description', 'amount', 'payment_method']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'payment_method': forms.Select(choices=[
                ('CASH', 'Cash'), ('CARD', 'Card'), ('ONLINE', 'Online'), ('CHEQUE', 'Cheque'),
            ]),
        }


class CashClosingForm(forms.ModelForm):
    class Meta:
        model = CashClosing
        fields = ['date', 'opening', 'counted', 'note']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}
