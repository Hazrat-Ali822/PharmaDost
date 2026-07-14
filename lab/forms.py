from django import forms
from django.forms import inlineformset_factory
from .models import TestOrder, TestResult, LabTest
from patients.models import Patient


class TestOrderCreateForm(forms.ModelForm):
    tests = forms.ModelMultipleChoiceField(
        queryset=LabTest.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text="Select one or more lab tests"
    )

    class Meta:
        model = TestOrder
        fields = ["patient", "tests"]  # status defaults to "Pending" on create

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["patient"].queryset = Patient.objects.all().order_by("full_name")
        self.user = user

    def save(self, commit=True):
        order = super().save(commit=False)
        if hasattr(self, "user") and self.user and self.user.is_authenticated:
            order.ordered_by = self.user
        if commit:
            order.save()
            tests_qs = self.cleaned_data.get("tests", [])
            for test in tests_qs:
                TestResult.objects.create(test_order=order, lab_test=test)
        return order


class TestResultForm(forms.ModelForm):
    class Meta:
        model = TestResult
        fields = ["lab_test", "result_value", "normal_range", "unit", "remarks"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            if not self.instance.normal_range and self.instance.lab_test:
                self.fields['normal_range'].initial = self.instance.lab_test.normal_range
            if not self.instance.unit and self.instance.lab_test:
                self.fields['unit'].initial = self.instance.lab_test.unit


TestResultFormSet = inlineformset_factory(
    parent_model=TestOrder,
    model=TestResult,
    form=TestResultForm,
    extra=0,
    can_delete=False
)