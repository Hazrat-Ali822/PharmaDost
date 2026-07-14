from django import forms
from .models import Appointment, Doctor, DoctorPayout
from patients.models import Patient


class DoctorForm(forms.ModelForm):
    class Meta:
        model = Doctor
        fields = ['user', 'full_name', 'specialty', 'pmdc_no', 'opd_fee', 'followup_fee',
                  'followup_valid_days', 'share_percent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        from saas.utils import get_current_hospital
        hospital = get_current_hospital()
        user_qs = User.objects.filter(role='DOCTOR', is_active=True)
        if hospital:
            user_qs = user_qs.filter(hospital=hospital)
        self.fields['user'].queryset = user_qs
        self.fields['user'].required = False
        self.fields['user'].label = "Linked User Account"
        self.fields['user'].help_text = "Select the login user account for this doctor."


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from patients.models import Patient
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True)
        self.fields['patient'].queryset = Patient.objects.filter(is_active=True)
