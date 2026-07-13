from django import forms

from patients.models import Patient
from .models import ImagingStudy


class ImagingStudyCreateForm(forms.ModelForm):
    """Order / register a new imaging study."""

    class Meta:
        model = ImagingStudy
        fields = ["patient", "modality", "study_name", "clinical_note", "price"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["patient"].queryset = Patient.objects.all().order_by("full_name")

    def save(self, commit=True):
        study = super().save(commit=False)
        if self.user and getattr(self.user, "is_authenticated", False):
            study.referred_by = self.user
        if commit:
            study.save()
        return study


class ImagingReportForm(forms.ModelForm):
    """Write the report: findings, impression, film + status."""

    class Meta:
        model = ImagingStudy
        fields = ["status", "findings", "impression", "image"]
        widgets = {
            "findings": forms.Textarea(attrs={"rows": 6}),
            "impression": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        study = super().save(commit=False)
        # stamp the performer the first time a report is written
        if self.user and getattr(self.user, "is_authenticated", False) and study.performed_by is None:
            study.performed_by = self.user
        if commit:
            study.save()
        return study
