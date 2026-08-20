from django import forms
from django.db.models import Q
from pharma_mgmt.widgets import DateInput, TimeInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import BirthCertificate, DeathCertificate


class BirthCertificateForm(forms.ModelForm):
    class Meta:
        model = BirthCertificate
        fields = ['child_name', 'sex', 'date_of_birth', 'time_of_birth', 'place_of_birth',
                  'weight_kg', 'mother_name', 'mother_cnic', 'father_name', 'father_cnic',
                  'address', 'phone', 'registered_on']
        widgets = {
            'date_of_birth': DateInput(),
            'time_of_birth': TimeInput(),
            'registered_on': DateInput(),
        }


class DeathCertificateForm(forms.ModelForm):
    class Meta:
        model = DeathCertificate
        fields = ['patient', 'deceased_name', 'sex', 'age_text', 'cnic', 'father_husband_name',
                  'date_of_death', 'time_of_death', 'place_of_death', 'cause_of_death',
                  'secondary_causes', 'manner', 'is_mlc', 'attending_doctor',
                  'next_of_kin', 'next_of_kin_cnic', 'address', 'registered_on']
        widgets = {
            'date_of_death': DateInput(),
            'time_of_death': TimeInput(),
            'registered_on': DateInput(),
            'secondary_causes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        pts = Patient.objects.all()
        docs = Doctor.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
            docs = scoped_doctors(user, docs)
        self.fields['patient'].queryset = pts.order_by('full_name')
        self.fields['patient'].required = False
        self.fields['attending_doctor'].queryset = docs
        self.fields['attending_doctor'].required = False
