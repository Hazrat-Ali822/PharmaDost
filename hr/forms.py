from django import forms

from accounts.models import User

from .models import LeaveRequest, SalaryPayment, StaffProfile


class StaffProfileForm(forms.ModelForm):
    class Meta:
        model = StaffProfile
        fields = ['designation', 'monthly_salary', 'joining_date', 'phone', 'cnic']
        widgets = {'joining_date': forms.DateInput(attrs={'type': 'date'})}


def _scope_user_field(field, user):
    qs = User.objects.filter(is_active=True)
    if user is not None and not user.is_superuser:
        qs = qs.filter(hospital=user.hospital)
    field.queryset = qs.order_by('email')


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['user', 'start_date', 'end_date', 'leave_type', 'reason']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_user_field(self.fields['user'], user)

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get('start_date'), cleaned.get('end_date')
        if s and e and e < s:
            raise forms.ValidationError('End date cannot be before the start date.')
        return cleaned


class SalaryPaymentForm(forms.ModelForm):
    class Meta:
        model = SalaryPayment
        fields = ['user', 'period', 'basic', 'allowances', 'deductions', 'paid_on', 'method', 'note']
        widgets = {'paid_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_user_field(self.fields['user'], user)
