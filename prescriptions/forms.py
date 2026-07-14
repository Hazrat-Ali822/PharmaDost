from django import forms
from django.forms import inlineformset_factory

from lab.models import LabTest
from imaging.models import ScanType
from .models import Prescription, PrescriptionItem, RxPreset, RxPresetItem


class PrescriptionForm(forms.ModelForm):
    # Lab tests + scans can be ticked right here so the doctor orders them with the
    # prescription. Both pull from the admin-managed price lists (LabTest / ScanType).
    tests = forms.ModelMultipleChoiceField(
        queryset=LabTest.objects.select_related('category').order_by('category__name', 'name'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Tick any lab tests to order along with this prescription.",
    )
    scans = forms.ModelMultipleChoiceField(
        queryset=ScanType.objects.filter(is_active=True).order_by('modality', 'name'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Tick any scans (ultrasound / x-ray / CT / MRI) to order.",
    )

    class Meta:
        model = Prescription
        fields = ['complaint', 'diagnosis', 'notes']


class PrescriptionItemForm(forms.ModelForm):
    class Meta:
        model = PrescriptionItem
        fields = ['medicine', 'custom_medicine_name', 'dosage', 'duration_days', 'instructions']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['medicine'].required = False
        self.fields['custom_medicine_name'].required = False
        self.fields['dosage'].required = False
        self.fields['duration_days'].required = False
        self.fields['instructions'].required = False

    def clean_duration_days(self):
        return self.cleaned_data.get('duration_days') or 1

    def clean(self):
        cleaned_data = super().clean()
        med = cleaned_data.get('medicine')
        custom = cleaned_data.get('custom_medicine_name')
        
        if not med and not custom:
            # Check if any other detail is filled (to validate half-filled rows)
            dosage = cleaned_data.get('dosage')
            instructions = cleaned_data.get('instructions')
            duration = cleaned_data.get('duration_days')
            if dosage or instructions or (duration and duration > 1):
                raise forms.ValidationError("Please select a medicine or enter a custom name.")
        return cleaned_data


# Many medicines per prescription (was a single form before).
PrescriptionItemFormSet = inlineformset_factory(
    Prescription,
    PrescriptionItem,
    form=PrescriptionItemForm,
    extra=1,
    can_delete=True,
)


class RxPresetForm(forms.ModelForm):
    class Meta:
        model = RxPreset
        fields = ['name', 'description']


class RxPresetItemForm(forms.ModelForm):
    class Meta:
        model = RxPresetItem
        fields = ['medicine', 'dosage', 'duration_days', 'instructions']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dosage'].required = False
        self.fields['duration_days'].required = False
        self.fields['instructions'].required = False

    def clean_duration_days(self):
        return self.cleaned_data.get('duration_days') or 3


RxPresetItemFormSet = inlineformset_factory(
    RxPreset,
    RxPresetItem,
    form=RxPresetItemForm,
    extra=2,
    can_delete=True,
)
