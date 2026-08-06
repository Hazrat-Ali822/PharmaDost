from django import forms

from patients.models import Patient

from .models import BloodDonor, BloodIssue, BloodUnit


class BloodDonorForm(forms.ModelForm):
    class Meta:
        model = BloodDonor
        fields = ['full_name', 'blood_group', 'gender', 'age', 'cnic', 'phone',
                  'last_donation_date', 'address', 'notes']
        widgets = {'last_donation_date': forms.DateInput(attrs={'type': 'date'})}


class BloodUnitForm(forms.ModelForm):
    class Meta:
        model = BloodUnit
        fields = ['bag_number', 'blood_group', 'component', 'donor', 'donor_name',
                  'donation_date', 'expiry_date', 'volume_ml', 'screening_done', 'notes']
        widgets = {
            'donation_date': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        donors = BloodDonor.objects.all()
        if user is not None and not user.is_superuser:
            donors = donors.filter(hospital=user.hospital)
        self.fields['donor'].queryset = donors.order_by('full_name')
        self.fields['donor'].required = False


class BloodIssueForm(forms.ModelForm):
    class Meta:
        model = BloodIssue
        fields = ['unit', 'patient', 'issued_on', 'cross_match', 'notes']
        widgets = {'issued_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        units = BloodUnit.objects.filter(status='AVAILABLE')
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            units = units.filter(hospital=user.hospital)
            pts = pts.filter(hospital=user.hospital)
        self.fields['unit'].queryset = units.order_by('expiry_date')
        self.fields['patient'].queryset = pts.order_by('full_name')
