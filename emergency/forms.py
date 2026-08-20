from django import forms
from django.db.models import Q
from saas.forms import TenantModelForm

from opd.models import Doctor
from opd.scoping import scoped_doctors
from patients.models import Patient

from .models import EmergencyCase


class EmergencyIntakeForm(TenantModelForm):
    """Triage-first intake. Either pick a registered patient or quick-add a new
    one (name is enough — the emergency room registers first, completes later)."""
    existing_patient = forms.ModelChoiceField(
        queryset=Patient.objects.all(), required=False, label='Existing patient')
    new_name = forms.CharField(required=False, label='New patient name')
    new_gender = forms.ChoiceField(required=False, label='Gender',
                                   choices=[('', '—'), ('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    new_age = forms.IntegerField(required=False, min_value=0, label='Age (years)')
    new_phone = forms.CharField(required=False, label='Phone')
    consultation_fee = forms.DecimalField(required=False, min_value=0,
                                          label='Emergency consultation fee (optional)')

    class Meta:
        model = EmergencyCase
        fields = ['triage', 'chief_complaint', 'mode_of_arrival', 'brought_by',
                  'is_mlc', 'mlc_no', 'pulse', 'bp', 'temp', 'spo2', 'attending_doctor',
                  'cost_price']
        labels = {'cost_price': 'Consumables cost (optional)'}
        help_texts = {'cost_price': 'What this cost the hospital — dressings, kit, drugs. Leave blank if you do not track it; the profit report then says so rather than reporting the whole charge as profit.'}

    def clean_cost_price(self):
        from decimal import Decimal
        return self.cleaned_data.get('cost_price') or Decimal('0.00')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional: a model DecimalField with a default is still a required
        # FORM field, and a blank box posts '' — which would reject a triage
        # entry over a bookkeeping number. See `clean_cost_price`.
        self.fields['cost_price'].required = False
        # Unconditional: `scoped_doctors` decides for itself what a superuser
        # or a hospital-less user may see. Guarding it with `if user is not
        # None` was the fail-open shape — a caller that forgot `user=` got
        # every tenant's doctors, and six forms carried that same copy.
        docs = scoped_doctors(user, Doctor.objects.filter(is_active=True))
        pts = Patient.objects.all()
        if user is not None and not user.is_superuser:
            pts = pts.filter(hospital=user.hospital)
        self.fields['attending_doctor'].queryset = docs
        self.fields['attending_doctor'].required = False
        self.fields['existing_patient'].queryset = pts

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('existing_patient') and not (cleaned.get('new_name') or '').strip():
            raise forms.ValidationError('Choose a registered patient or enter a new patient name.')
        return cleaned


class DispositionForm(TenantModelForm):
    class Meta:
        model = EmergencyCase
        fields = ['disposition', 'disposition_notes', 'attending_doctor',
                  'pulse', 'bp', 'temp', 'spo2']
