from django import forms
from .models import Appointment, Doctor, DoctorPayout
from patients.models import Patient


class DoctorForm(forms.ModelForm):
    class Meta:
        model = Doctor
        fields = ['full_name', 'specialty', 'pmdc_no', 'opd_fee', 'followup_fee',
                  'followup_valid_days', 'share_percent']


class DoctorPayoutForm(forms.ModelForm):
    class Meta:
        model = DoctorPayout
        fields = ['date', 'amount', 'payment_method', 'note']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'payment_method': forms.Select(choices=[
                ('CASH', 'Cash'), ('BANK', 'Bank Transfer'), ('CHEQUE', 'Cheque'),
            ]),
        }


class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['patient', 'doctor', 'appointment_date', 'slot_time', 'token_no', 'visit_type', 'status']
        widgets = {
            'appointment_date': forms.DateInput(attrs={'type': 'date'}),
            'slot_time': forms.TimeInput(attrs={'type': 'time'}),
        }
