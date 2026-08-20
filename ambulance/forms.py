from django import forms
from saas.forms import TenantModelForm

from .models import Ambulance, AmbulanceDriver, AmbulanceTrip


class AmbulanceForm(TenantModelForm):
    class Meta:
        model = Ambulance
        fields = ['registration_no', 'label', 'vehicle_type', 'driver', 'phone',
                  'base_charge', 'per_km_charge', 'waiting_charge_per_hour',
                  'cost_price', 'status', 'is_active', 'notes']
        widgets = {
            'registration_no': forms.TextInput(attrs={'placeholder': 'LEA-1234'}),
            'label': forms.TextInput(attrs={'placeholder': 'Ambulance 1'}),
            'vehicle_type': forms.TextInput(attrs={
                'list': 'ambulance-types', 'placeholder': 'Basic / ALS / ICU-equipped'}),
            'notes': forms.TextInput(attrs={'placeholder': 'optional'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-request, never at class level: a class-level queryset is evaluated
        # once at import with no tenant bound, so it would list — and accept —
        # another hospital's driver.
        self.fields['driver'].queryset = _scoped(AmbulanceDriver, user).filter(is_active=True)
        self.fields['driver'].required = False


class AmbulanceDriverForm(TenantModelForm):
    class Meta:
        model = AmbulanceDriver
        fields = ['full_name', 'phone', 'licence_no', 'cnic', 'address',
                  'is_active', 'notes']
        widgets = {
            'notes': forms.TextInput(attrs={'placeholder': 'optional'}),
        }


class AmbulanceTripForm(TenantModelForm):
    """Booking a run. Charges are left blank on the form and filled from the
    vehicle's rates at dispatch — typing one overrides it for this trip only."""

    class Meta:
        model = AmbulanceTrip
        fields = ['ambulance', 'driver', 'trip_type', 'patient', 'contact_name',
                  'contact_phone', 'from_location', 'to_location', 'distance_km',
                  'waiting_hours', 'called_at', 'base_charge', 'per_km_charge',
                  'waiting_charge_per_hour', 'notes']
        widgets = {
            'called_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'from_location': forms.TextInput(attrs={'placeholder': 'Pick-up address'}),
            'to_location': forms.TextInput(attrs={'placeholder': 'Drop address / facility'}),
            'contact_name': forms.TextInput(attrs={'placeholder': 'Who called'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'optional'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from patients.models import Patient

        self.fields['ambulance'].queryset = _scoped(Ambulance, user).filter(is_active=True)
        self.fields['driver'].queryset = _scoped(AmbulanceDriver, user).filter(is_active=True)
        self.fields['patient'].queryset = _scoped(Patient, user)
        for name in ('driver', 'patient', 'contact_name', 'contact_phone',
                     'base_charge', 'per_km_charge', 'waiting_charge_per_hour',
                     'waiting_hours', 'distance_km', 'notes'):
            self.fields[name].required = False
        self.fields['patient'].help_text = (
            'Leave empty for a call with no registered patient — a body transfer, '
            'or someone else\'s patient being moved.')

    def clean(self):
        cleaned = super().clean()
        # A trip has to be traceable to somebody: either a registered patient or
        # at least a name/number for whoever called. Neither means a record that
        # cannot be looked up when a family asks or an auditor does.
        if not cleaned.get('patient') and not (cleaned.get('contact_name')
                                               or cleaned.get('contact_phone')):
            raise forms.ValidationError(
                'Pick a patient, or give the caller\'s name or phone number.')
        # A blank money box means "use the vehicle's rate" (dispatch_trip fills
        # it), and a blank distance means "not measured yet" — both are zero on
        # the record. The scale has to match each model field: distance_km and
        # waiting_hours allow one decimal place, so `0.00` fails model
        # validation and the whole form comes back with nothing on screen to
        # explain why.
        from decimal import Decimal
        for name in ('base_charge', 'per_km_charge', 'waiting_charge_per_hour'):
            if cleaned.get(name) is None:
                cleaned[name] = Decimal('0.00')
        for name in ('distance_km', 'waiting_hours'):
            if cleaned.get(name) is None:
                cleaned[name] = Decimal('0.0')
        return cleaned


def _scoped(model, user):
    """Fail-closed tenant scope: key on superuser, never on "has a hospital"."""
    manager = getattr(model, 'all_objects', model.objects)
    qs = manager.all()
    if not getattr(user, 'is_superuser', False):
        qs = qs.filter(hospital=getattr(user, 'hospital', None))
    return qs
