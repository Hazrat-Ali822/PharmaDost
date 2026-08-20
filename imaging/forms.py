from django import forms
from saas.forms import TenantModelForm

from patients.models import Patient
from .models import ImagingStudy, ScanType


def _catalogue_cost(study_name):
    """The consumable cost this tenant recorded for a scan of this name.

    Matched on the name because `ImagingStudy` has no foreign key to
    `ScanType` — radiology types the study name and its price. An unmatched
    name (a one-off, a typo, a scan not in the list) gives 0, which every
    reader treats as "not recorded" rather than free.
    """
    from decimal import Decimal

    name = (study_name or "").strip()
    if not name:
        return Decimal("0.00")
    match = (ScanType.objects
             .filter(name__iexact=name, cost_price__gt=0)
             .values_list("cost_price", flat=True)
             .first())
    return match or Decimal("0.00")


class ImagingStudyCreateForm(TenantModelForm):
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
        if not study.cost_price:
            study.cost_price = _catalogue_cost(study.study_name)
        if commit:
            study.save()
        return study


class ImagingReportForm(TenantModelForm):
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
