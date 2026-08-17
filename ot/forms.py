from decimal import Decimal

from django import forms
from .models import SurgeryCategory, SurgeryProcedure, SurgeryRecord
from patients.models import Patient
from ipd.models import Admission
from opd.models import Doctor
from opd.scoping import scoped_doctors

class SurgeryCategoryForm(forms.ModelForm):
    class Meta:
        model = SurgeryCategory
        fields = ['name']

class SurgeryProcedureForm(forms.ModelForm):
    class Meta:
        model = SurgeryProcedure
        fields = ['name', 'category', 'standard_charge', 'ot_charge',
                  'anesthesia_charge', 'consumables_charge', 'cost_price']
        labels = {
            'standard_charge': "Surgeon's fee",
            'ot_charge': 'Theatre / OT room charge',
            'anesthesia_charge': 'Anaesthesia charge',
            'consumables_charge': 'Consumables charge',
            'cost_price': 'Your own cost (optional)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A hospital that does not itemise leaves these blank and bills exactly
        # as it did before. Required fields here would also have broken the
        # offline `procedure` handler, which posts the old payload.
        for name in ('ot_charge', 'anesthesia_charge', 'consumables_charge',
                     'cost_price'):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        for name in ('ot_charge', 'anesthesia_charge', 'consumables_charge',
                     'cost_price'):
            if cleaned.get(name) in (None, ''):
                cleaned[name] = Decimal('0.00')
        return cleaned

class SurgeryRecordForm(forms.ModelForm):
    class Meta:
        model = SurgeryRecord
        fields = [
            'patient', 'admission', 'procedure', 'start_time', 'end_time',
            'lead_surgeon', 'surgical_team', 'anesthesia_type',
            'surgeon_charge', 'ot_charge', 'anesthesia_charge',
            'consumables_charge', 'operation_notes', 'outcome'
        ]
        labels = {
            'surgeon_charge': "Surgeon's fee",
            'ot_charge': 'Theatre / OT room charge',
            'anesthesia_charge': 'Anaesthesia charge',
            'consumables_charge': 'Consumables charge',
        }
        help_texts = {
            'surgeon_charge': 'Leave at 0 to use the procedure’s catalogue rate.',
        }
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'surgical_team': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Assistant surgeons, anesthetist, nurses names...'}),
            'operation_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Detailed surgical procedure findings...'}),
        }

    def __init__(self, *args, **kwargs):
        # `user` is what scopes the surgeon list; see below.
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['patient'].queryset = Patient.objects.all().order_by('full_name')
        self.fields['admission'].queryset = Admission.objects.filter(status='Admitted').order_by('-admission_date')
        self.fields['procedure'].queryset = SurgeryProcedure.objects.all().order_by('name')
        # `Doctor` has no `hospital` column and no TenantManager, so
        # `Doctor.objects.all()` is every tenant's doctors — and a ModelChoiceField
        # validates the posted id against its own queryset, so this both listed and
        # *accepted* another hospital's surgeon. `scoped_doctors` is the one named
        # filter for this model; see CLAUDE.md on view-level scoping.
        self.fields['lead_surgeon'].queryset = scoped_doctors(self.user).order_by('full_name')
        # Charges are optional on the form: blank means "use the catalogue rate",
        # which `ot.services.apply_procedure_defaults` fills in at save.
        for name in ('surgeon_charge', 'ot_charge', 'anesthesia_charge',
                     'consumables_charge'):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        # Blank means "use the catalogue rate", which the service fills in at
        # save; the model column is NOT NULL, so it has to be a number by then.
        for name in ('surgeon_charge', 'ot_charge', 'anesthesia_charge',
                     'consumables_charge'):
            if cleaned.get(name) in (None, ''):
                cleaned[name] = Decimal('0.00')
        return cleaned
