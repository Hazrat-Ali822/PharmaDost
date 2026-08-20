from django import forms
from django.db.models import Q
from pharma_mgmt.widgets import DateInput

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import AntenatalVisit, Delivery, Pregnancy


def _scope_doctor(field, user):
    docs = Doctor.objects.filter(is_active=True)
    if user is not None and not user.is_superuser:
        docs = scoped_doctors(user, docs)
    field.queryset = docs
    field.required = False


class PregnancyForm(forms.ModelForm):
    class Meta:
        model = Pregnancy
        fields = ['mother', 'husband_name', 'lmp', 'gravida', 'para', 'abortions',
                  'blood_group', 'high_risk', 'risk_notes']
        widgets = {'lmp': DateInput()}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['mother'].queryset = pts.order_by('full_name')


class AntenatalVisitForm(forms.ModelForm):
    class Meta:
        model = AntenatalVisit
        fields = ['date', 'weight', 'bp', 'fundal_height', 'fhr', 'complaints',
                  'notes', 'next_visit', 'seen_by']
        widgets = {
            'date': DateInput(),
            'next_visit': DateInput(),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_doctor(self.fields['seen_by'], user)


class DeliveryForm(forms.ModelForm):
    consultation_fee = forms.DecimalField(required=False, min_value=0,
                                          label='Delivery charge (optional)')

    class Meta:
        model = Delivery
        fields = ['mother', 'delivered_at', 'delivery_type', 'outcome', 'conducted_by', 'notes']
        widgets = {
            'delivered_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['mother'].queryset = pts.order_by('full_name')
        _scope_doctor(self.fields['conducted_by'], user)
