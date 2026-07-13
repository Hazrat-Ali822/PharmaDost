from django import forms
from django.forms import inlineformset_factory

from lab.models import LabTest
from imaging.models import ScanType
from .models import Prescription, PrescriptionItem


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
    """A medicine line. Only the medicine itself is required — dosage/days are optional
    so a half-filled row never silently blocks the whole prescription from saving."""
    class Meta:
        model = PrescriptionItem
        fields = ['medicine', 'dosage', 'duration_days', 'instructions']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dosage'].required = False
        self.fields['duration_days'].required = False
        self.fields['instructions'].required = False

    def clean_duration_days(self):
        return self.cleaned_data.get('duration_days') or 1


# Many medicines per prescription (was a single form before).
PrescriptionItemFormSet = inlineformset_factory(
    Prescription,
    PrescriptionItem,
    form=PrescriptionItemForm,
    extra=1,
    can_delete=True,
)
