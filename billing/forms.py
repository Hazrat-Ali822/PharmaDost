from django import forms
from .models import Invoice, Expense, CashClosing


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['patient', 'appointment', 'payment_method', 'discount', 'paid']

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # This screen bills an appointment's consultation, so the appointment is
        # mandatory — without it create_opd_invoice(None) would crash on None.doctor.
        self.fields['appointment'].required = True
        # Appointment has no hospital column and no tenant manager, so its default
        # queryset lists EVERY tenant's appointments. Scope it (and the patient list)
        # to the signed-in user's hospital — fail-closed on superuser, never on
        # "has a hospital", so a hospital-less user sees only hospital-less rows.
        if user is not None and not user.is_superuser:
            from patients.models import Patient
            self.fields['patient'].queryset = Patient.objects.filter(hospital=user.hospital)
            self.fields['appointment'].queryset = self.fields['appointment'].queryset.filter(
                patient__hospital=user.hospital)


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
