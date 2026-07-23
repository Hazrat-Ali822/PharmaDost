from django import forms
from django.forms import inlineformset_factory
from .models import (Appointment, Department, Doctor, DoctorPayout, DoctorSchedule)
from patients.models import Patient


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Gynaecology'}),
            'description': forms.TextInput(attrs={'placeholder': 'Optional — shown to reception'}),
        }


class DoctorForm(forms.ModelForm):
    class Meta:
        model = Doctor
        fields = ['user', 'full_name', 'department', 'specialty', 'pmdc_no',
                  'opd_fee', 'followup_fee', 'followup_valid_days', 'share_percent']

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
        # Tenant-scoped by Department's manager.
        self.fields['department'].queryset = Department.objects.filter(is_active=True)
        self.fields['department'].help_text = (
            "Reception picks a department first, then sees only its doctors.")


class DoctorScheduleForm(forms.ModelForm):
    class Meta:
        model = DoctorSchedule
        fields = ['weekday', 'start_time', 'end_time']
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_time'), cleaned.get('end_time')
        if start and end and end <= start:
            raise forms.ValidationError('The finishing time must be after the starting time.')
        return cleaned


# A doctor often sits twice a day (morning + evening OPD), so timings are a set
# of rows rather than one start/end pair on the doctor.
DoctorScheduleFormSet = inlineformset_factory(
    Doctor, DoctorSchedule, form=DoctorScheduleForm, extra=3, can_delete=True)


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


class VisitForm(forms.Form):
    """The reception desk's half of a visit: which department, which doctor, what
    kind of visit. Paired with `PatientForm` for a walk-in, or used alone once an
    existing patient has been looked up.

    Not a ModelForm: the appointment's token is allocated in `Appointment.save()`
    and the patient may not exist yet when this is validated.
    """
    department = forms.ModelChoiceField(queryset=Department.objects.none(),
                                        required=False, empty_label='All departments')
    doctor = forms.ModelChoiceField(queryset=Doctor.objects.none())
    visit_type = forms.ChoiceField(choices=Appointment.VISIT_CHOICES, initial='OPD')
    appointment_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    slot_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'type': 'time'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        self.fields['department'].queryset = Department.objects.filter(is_active=True)
        # Every active doctor is valid to POST — reception can knowingly book an
        # off-duty doctor for an emergency. The screen just hides them by default.
        self.fields['doctor'].queryset = Doctor.objects.filter(is_active=True)
        now = timezone.localtime()
        self.fields['appointment_date'].initial = now.date()
        self.fields['slot_time'].initial = now.time().strftime('%H:%M')

    def clean(self):
        cleaned = super().clean()
        department, doctor = cleaned.get('department'), cleaned.get('doctor')
        if department and doctor and doctor.department_id != department.pk:
            self.add_error('doctor', f'{doctor.full_name} is not in {department.name}.')
        return cleaned
