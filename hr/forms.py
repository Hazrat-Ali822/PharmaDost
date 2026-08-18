from datetime import date
from django import forms

from accounts.models import NO_LOGIN_EMAIL_DOMAIN, NO_LOGIN_ROLE, User

from .models import LeaveRequest, SalaryPayment, StaffProfile


class StaffProfileForm(forms.ModelForm):
    class Meta:
        model = StaffProfile
        fields = [
            'photo', 'designation', 'monthly_salary', 'allowed_monthly_leaves',
            'enable_absence_deduction', 'deduction_per_absent_day',
            'joining_date', 'phone', 'cnic', 'address', 'emergency_contact'
        ]
        widgets = {'joining_date': forms.DateInput(attrs={'type': 'date'})}


def _employee_label(u):
    """What the staff dropdowns show for one person.

    They showed `str(user)` — the email address and the role — so paying a ward
    boy meant picking `staff-d448b32bae@no-login.invalid (Pharmacist)` out of a
    list, which is neither his name nor his job. Payroll is chosen by name.
    """
    profile = getattr(u, 'staff_profile', None)
    job = (getattr(profile, 'designation', '') or '').strip()
    if not job and u.signs_in:
        job = u.get_role_display()
    return f'{u.display_name} — {job}' if job else u.display_name


def _scope_user_field(field, user):
    qs = User.objects.filter(is_active=True).select_related('staff_profile')
    if user is not None and not user.is_superuser:
        qs = qs.filter(hospital=user.hospital)
    # By name, not by address — the address is a placeholder for most of payroll.
    field.queryset = qs.order_by('first_name', 'last_name', 'email')
    field.label_from_instance = _employee_label
    field.label = 'Employee'
    field.empty_label = 'Choose an employee…'


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

    # How many months back the picker offers. Payroll is entered for the month
    # just gone, occasionally one before that; a longer list is a longer scroll.
    PERIOD_MONTHS = 18

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_user_field(self.fields['user'], user)

        # `period` is a free-text CharField, and a free-text period cannot be
        # reconciled: the ledger held "August 2026" next to "Last month", which
        # names a different month every time it is read and cannot be summed,
        # filtered or compared with anything. The column stays text (old rows
        # must keep reading correctly, and a hospital may want "Aug 2026 bonus"),
        # but the FORM now offers real months.
        self.fields['period'] = forms.ChoiceField(
            choices=self._period_choices(), label='Salary for',
            help_text='The month this payment covers — not the day it is paid.')

    @classmethod
    def _period_choices(cls):
        from django.utils import timezone
        today = timezone.localdate()
        months, year, month = [], today.year, today.month
        for _ in range(cls.PERIOD_MONTHS):
            label = date(year, month, 1).strftime('%B %Y')
            months.append((label, label))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        return months

    def clean_period(self):
        # A stored value from before this was a dropdown (or from an import)
        # must not be rejected when an old payslip is re-saved.
        return self.cleaned_data.get('period') or self.initial.get('period', '')


class EmployeeForm(forms.Form):
    """Add somebody who works here.

    Separate from `StaffProfileForm`, which edits an existing person, because
    creating one has to answer a question editing never does: **does this person
    need a login?**

    Most of a clinic's payroll does not. A guard, a cleaner, a ward boy, a
    driver — they are on the attendance machine and on the salary sheet and they
    never touch a screen. Until now the only way to put somebody on the
    attendance sheet was to create a `User`, which needs a unique email address,
    so adding a cleaner meant inventing one. That is the sort of thing that gets
    done as `cleaner1@gmail.com` and then somebody wonders whose account it is.

    So the login half is optional and off by default. Without it the person
    still gets a `User` row — `Attendance`, `LeaveRequest` and `SalaryPayment`
    all point at one, and re-pointing payroll at something else is a much larger
    change than this problem justifies — but it is a row nobody can sign in as:
    an unusable password, and `custom_features = []`, which is the documented
    "exactly this set, even empty" override. Two independent reasons it cannot
    be used, because one of them being wrong should not be enough.
    """

    full_name = forms.CharField(max_length=120, label='Full name')
    designation = forms.CharField(max_length=100, required=False,
                                  help_text='e.g. Ward boy, Guard, Receptionist')
    monthly_salary = forms.DecimalField(max_digits=12, decimal_places=2,
                                        required=False, min_value=0)
    phone = forms.CharField(max_length=20, required=False)
    cnic = forms.CharField(max_length=20, required=False)
    biometric_id = forms.CharField(
        max_length=32, required=False, label='Enrolment number on the machine',
        help_text='The number the attendance machine gave them. Leave blank if '
                  'they are not enrolled yet — you can fill it in later.')
    joining_date = forms.DateField(required=False,
                                   widget=forms.DateInput(attrs={'type': 'date'}))

    wants_login = forms.BooleanField(
        required=False, label='This person also signs in to the system')
    email = forms.EmailField(required=False)
    password = forms.CharField(required=False, widget=forms.PasswordInput,
                               min_length=6)
    # "No system access" is what NOT ticking the box already means, so offering
    # it here as well would be two controls for one decision.
    role = forms.ChoiceField(
        choices=[(k, v) for k, v in User.ROLE_CHOICES if k != NO_LOGIN_ROLE],
        required=False)

    def __init__(self, *args, hospital=None, **kwargs):
        self.hospital = hospital
        super().__init__(*args, **kwargs)

    def clean_biometric_id(self):
        value = (self.cleaned_data.get('biometric_id') or '').strip()
        if value and StaffProfile.all_objects.filter(
                hospital=self.hospital, biometric_id=value).exists():
            raise forms.ValidationError(
                f'Enrolment number {value} is already on somebody else. '
                'The machine only has one of each.')
        return value

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('wants_login'):
            return cleaned
        email = (cleaned.get('email') or '').strip().lower()
        if not email:
            self.add_error('email', 'A login needs an email address.')
        elif User.objects.filter(email__iexact=email).exists():
            self.add_error('email', 'Somebody already signs in with that address.')
        if not cleaned.get('password'):
            self.add_error('password', 'Set a password they can sign in with.')
        if not cleaned.get('role'):
            self.add_error('role', 'Pick what they do in the system.')
        return cleaned

    def save(self):
        import uuid

        from django.db import transaction

        data = self.cleaned_data
        name = data['full_name'].strip()
        first, _, last = name.partition(' ')

        with transaction.atomic():
            if data.get('wants_login'):
                user = User.objects.create_user(
                    email=data['email'].strip().lower(), password=data['password'],
                    role=data['role'], hospital=self.hospital)
            else:
                # An address nobody can receive at, in a domain that cannot
                # exist, so it never gets mistaken for a real one or emailed.
                # role=NO_LOGIN_ROLE, not a real one. This used to say
                # 'PHARMACIST' — chosen only because it is the model default —
                # so a ward boy appeared as a Pharmacist in the staff list, the
                # users list and the attendance sheet, and his generated address
                # was printed beside it as though it were real.
                user = User.objects.create(
                    email=f'staff-{uuid.uuid4().hex[:10]}{NO_LOGIN_EMAIL_DOMAIN}',
                    role=NO_LOGIN_ROLE, hospital=self.hospital,
                    custom_features=[])
                user.set_unusable_password()
                user.save(update_fields=['password'])
            user.first_name, user.last_name = first[:150], last[:150]
            user.save(update_fields=['first_name', 'last_name'])

            profile, _created = StaffProfile.all_objects.get_or_create(
                user=user, defaults={'hospital': self.hospital})
            profile.hospital = self.hospital
            profile.designation = data.get('designation') or ''
            profile.monthly_salary = data.get('monthly_salary') or 0
            profile.phone = data.get('phone') or ''
            profile.cnic = data.get('cnic') or ''
            profile.biometric_id = data.get('biometric_id') or ''
            profile.joining_date = data.get('joining_date')
            profile.save()
        return profile
