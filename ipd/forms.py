from django import forms

from opd.scoping import scoped_doctors
from .models import (Ward, Bed, Admission, DoctorRound, VitalsObservation,
                    FluidBalanceEntry, NursingNote, ShiftHandover, CareTask)
from patients.models import Patient
from opd.models import Doctor

class WardForm(forms.ModelForm):
    class Meta:
        model = Ward
        fields = ['name', 'ward_type', 'daily_rate', 'in_charge']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ward In-charge is a senior nurse (admins may run a ward too).
        from accounts.models import User
        self.fields['in_charge'].queryset = User.objects.filter(
            is_active=True, role__in=['NURSE', 'ADMIN']).order_by('first_name', 'email')
        self.fields['in_charge'].required = False
        self.fields['in_charge'].label = 'Ward In-charge (senior nurse)'

class BedForm(forms.ModelForm):
    class Meta:
        model = Bed
        fields = ['bed_number', 'ward', 'status']

class AdmissionForm(forms.ModelForm):
    class Meta:
        model = Admission
        fields = ['patient', 'bed', 'attending_doctor', 'admission_reason']
        widgets = {
            'admission_reason': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # `Patient` has a TenantManager, so `objects` is already scoped.
        # `Doctor` has NO hospital column and no manager of its own, so an
        # unscoped `Doctor.objects.all()` here listed every tenant's doctors in
        # the admit form — and a ModelChoiceField validates the POSTed id
        # against its own queryset, so it would also have *accepted* one. Same
        # defect the theatre form had. Always go through `scoped_doctors`.
        self.fields['patient'].queryset = Patient.objects.all().order_by('full_name')
        self.fields['attending_doctor'].queryset = scoped_doctors(
            self.user, Doctor.objects.filter(is_active=True)).order_by('full_name')
        
        # Only show available beds, plus the currently assigned bed if editing
        avail_beds = Bed.objects.filter(status='Available')
        if self.instance and self.instance.pk and self.instance.bed:
            avail_beds = avail_beds | Bed.objects.filter(pk=self.instance.bed.pk)
        self.fields['bed'].queryset = avail_beds.distinct()

class DoctorRoundForm(forms.ModelForm):
    class Meta:
        model = DoctorRound
        fields = ['vitals_temp', 'vitals_bp', 'vitals_pulse', 'clinical_notes', 'prescription_updates']
        widgets = {
            'clinical_notes': forms.Textarea(attrs={'rows': 3}),
            'prescription_updates': forms.Textarea(attrs={'rows': 2}),
        }

from .models import Ward, Bed, Admission, DoctorRound, MedicationLog

class DischargeForm(forms.ModelForm):
    class Meta:
        model = Admission
        fields = ['discharge_notes']
        widgets = {
            'discharge_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Enter discharge summary and medications advised...'}),
        }

class MedicationLogForm(forms.ModelForm):
    """`medicine` is filled in by the search box (hidden field) when the nurse picks
    a catalogue item; leaving it empty records an off-catalogue drug with no stock
    movement and no charge.

    The search box is backed by the DOCTOR'S ORDERS for this patient, not the whole
    pharmacy catalogue — the ward gives what was prescribed, and a list of every
    drug in the building is noise a nurse has to filter at the bedside. The full
    catalogue stays one toggle away for the case where the order was written on
    paper or during a round."""

    class Meta:
        model = MedicationLog
        fields = ['medicine', 'medicine_name', 'dosage', 'quantity', 'source',
                  'administered_at', 'notes']
        widgets = {
            'medicine': forms.HiddenInput(),
            # Backed by a <datalist> of this patient's prescribed drugs (see the
            # template). Deliberately still free text: a ward may administer
            # something the pharmacy does not stock, and that must remain recordable.
            'medicine_name': forms.TextInput(attrs={
                'list': 'prescribed-medicines',
                'autocomplete': 'off',
                'placeholder': "Pick from the doctor's orders, or type a name…",
            }),
            'quantity': forms.NumberInput(attrs={'min': 1, 'step': 1}),
            'source': forms.RadioSelect(),
            'administered_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.TextInput(attrs={'placeholder': 'e.g. given after lunch, patient tolerated well'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Medicine
        # Tenant-scoped by Medicine's manager; also validates the posted id really
        # belongs to this hospital.
        self.fields['medicine'].queryset = Medicine.objects.filter(is_active=True)
        self.fields['medicine'].required = False
        self.fields['quantity'].label = 'Quantity given'
        self.fields['source'].help_text = (
            "Only pharmacy stock is deducted and billed. A supply the patient "
            "already had is recorded on the chart only."
        )

    def clean_quantity(self):
        qty = self.cleaned_data.get('quantity') or 1
        if qty < 1:
            raise forms.ValidationError('Quantity must be at least 1.')
        return qty


class VitalsObservationForm(forms.ModelForm):
    """The nursing vitals set. Every measured field is optional so a partial obs
    is still saveable (MEWS marks it incomplete); the point is never to block the
    nurse from recording what they did take."""
    class Meta:
        model = VitalsObservation
        fields = ['taken_at', 'temperature', 'pulse', 'respiratory_rate',
                  'systolic_bp', 'diastolic_bp', 'spo2', 'consciousness',
                  'pain_score', 'blood_glucose', 'notes']
        widgets = {
            'taken_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'consciousness': forms.RadioSelect(),
            'notes': forms.TextInput(attrs={'placeholder': 'e.g. resting, on O₂ 2L'}),
        }

    # Physiological bounds, deliberately WIDE. These are not clinical judgement
    # — a MEWS of 3 is the system's opinion and a nurse may disagree — they are
    # a check that the number came from a patient at all. SpO2 500%, a pain
    # score of 99/10 and a temperature of 999 F all saved, and a nonsense vital
    # in a chart is worse than a missing one because MEWS then scores it.
    #
    # (low, high, unit) — inclusive.
    RANGES = {
        'temperature': (80, 115, '°F'),
        'pulse': (20, 300, 'bpm'),
        'respiratory_rate': (4, 80, 'breaths/min'),
        'systolic_bp': (40, 300, 'mmHg'),
        'diastolic_bp': (20, 200, 'mmHg'),
        'spo2': (10, 100, '%'),
        'pain_score': (0, 10, '/10'),
        'blood_glucose': (10, 1000, 'mg/dL'),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mirrored onto the widget so the phone keyboard and the browser stop it
        # before a round trip. The server check below is the one that counts.
        for name, (low, high, unit) in self.RANGES.items():
            if name in self.fields:
                self.fields[name].widget.attrs.update(
                    {'min': low, 'max': high, 'inputmode': 'decimal'})

    def clean(self):
        cleaned = super().clean()
        for name, (low, high, unit) in self.RANGES.items():
            value = cleaned.get(name)
            if value in (None, ''):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue          # vitals are free-text in places; see CLAUDE.md
            if not (low <= number <= high):
                self.add_error(name, f'{number:g}{unit} is outside anything a '
                                     f'patient can be — expected {low}–{high}{unit}.')
        # At least one measured value — an empty obs is not an observation.
        measured = ['temperature', 'pulse', 'respiratory_rate', 'systolic_bp',
                    'diastolic_bp', 'spo2', 'pain_score', 'blood_glucose']
        if not any(cleaned.get(f) is not None for f in measured):
            raise forms.ValidationError('Record at least one vital sign.')
        return cleaned


class FluidBalanceEntryForm(forms.ModelForm):
    class Meta:
        model = FluidBalanceEntry
        fields = ['recorded_at', 'direction', 'kind', 'volume_ml', 'notes']
        widgets = {
            'recorded_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'direction': forms.RadioSelect(),
            'kind': forms.TextInput(attrs={'list': 'fluid-kinds',
                                           'placeholder': 'IV fluid, Oral, Urine…'}),
            'notes': forms.TextInput(attrs={'placeholder': 'optional'}),
        }


class _ShiftScopedForm(forms.ModelForm):
    """Fills the `shift` dropdown with **this hospital's** shifts.

    The queryset is set here, per request, and never as a class attribute: a
    class-level `queryset=Shift.objects.all()` is evaluated once at import with
    no tenant bound, so `TenantManager` hands back every hospital's rows and the
    field would then *accept* a POST naming another tenant's shift. Same trap the
    lab-order and consent forms document.
    """

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from hr.models import Shift
        hospital = None if getattr(user, 'is_superuser', False) else getattr(user, 'hospital', None)
        self.fields['shift'].queryset = Shift.for_hospital(hospital)
        self.fields['shift'].empty_label = None


class NursingNoteForm(_ShiftScopedForm):
    class Meta:
        model = NursingNote
        fields = ['noted_at', 'shift', 'note']
        widgets = {
            'noted_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'note': forms.Textarea(attrs={'rows': 4,
                     'placeholder': 'What you observed, what you did, how the patient responded…'}),
        }


class ShiftHandoverForm(_ShiftScopedForm):
    class Meta:
        model = ShiftHandover
        fields = ['date', 'shift', 'situation', 'background', 'assessment', 'recommendation']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'situation': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Current problem / why here'}),
            'background': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Relevant history'}),
            'assessment': forms.Textarea(attrs={'rows': 2, 'placeholder': 'How they are now — obs, concerns'}),
            'recommendation': forms.Textarea(attrs={'rows': 2, 'placeholder': 'What the next shift must do'}),
        }


class CareTaskForm(forms.ModelForm):
    class Meta:
        model = CareTask
        fields = ['task', 'done_at', 'notes']
        widgets = {
            'done_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.TextInput(attrs={'placeholder': 'optional'}),
        }
