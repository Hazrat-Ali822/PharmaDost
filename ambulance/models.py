"""Ambulance fleet, drivers and trips.

Before this the word "ambulance" appeared in exactly one place in the codebase —
a *mode of arrival* dropdown value on `EmergencyCase`. A hospital could record
that a patient arrived by ambulance and nothing else: not which vehicle went, not
who drove it, not when it was called or how far it went, and not what it cost.

Three things shape this app:

**Rates are configured, not hardcoded.** Every hospital charges differently —
some a flat call-out, some by the kilometre, some nothing at all for a transfer
they caused. So each vehicle carries its own `base_charge`, `per_km_charge` and
`waiting_charge_per_hour`, all defaulting to 0, and a trip **freezes** them onto
itself when it is created. Repricing the fleet tomorrow cannot rewrite what a
family was charged today — the same rule `SaleItem.cost_price`,
`MedicationLog.unit_price` and `SurgeryRecord`'s four charges already follow.

**A trip often has no patient.** Body transfers, inter-facility moves for
somebody else's patient, a call that turns out to be a false alarm — insisting on
a `Patient` row would mean either refusing to record real work or registering
patients who are not patients. `patient` is nullable and `contact_name` /
`contact_phone` carry the caller instead.

**Nothing here dispatches by GPS.** The board tells the operator which vehicles
are free and which are out; the times are what the operator types.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from patients.models import Patient
from saas.utils import TenantManager


class AmbulanceDriver(models.Model):
    """A driver. Deliberately *not* a `User` — ambulance drivers rarely have a
    login, and requiring an account to record who drove would mean either
    creating dead accounts or leaving the field blank."""

    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    licence_no = models.CharField(max_length=40, blank=True)
    cnic = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='ambulance_drivers')

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('full_name',)

    def __str__(self):
        return self.full_name


class Ambulance(models.Model):
    """One vehicle, with its own rates."""

    STATUS_AVAILABLE = 'AVAILABLE'
    STATUS_ON_TRIP = 'ON_TRIP'
    STATUS_MAINTENANCE = 'MAINTENANCE'
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, 'Available'),
        (STATUS_ON_TRIP, 'On a trip'),
        (STATUS_MAINTENANCE, 'Out of service'),
    ]

    registration_no = models.CharField(
        max_length=30, help_text='Number plate, e.g. LEA-1234')
    label = models.CharField(
        max_length=60, blank=True,
        help_text='What staff call it — "Ambulance 1", "ICU Van". Optional.')
    # Free text rather than choices: "Basic", "ALS", "ICU-equipped", "Mortuary
    # van", "Bike ambulance" — the mix differs from one hospital to the next.
    vehicle_type = models.CharField(
        max_length=60, blank=True,
        help_text='e.g. Basic, ALS, ICU-equipped, Mortuary van')
    driver = models.ForeignKey(AmbulanceDriver, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='ambulances',
                               help_text='Usual driver — a trip can name another')
    phone = models.CharField(max_length=20, blank=True,
                             help_text='Number to call for this vehicle')

    # Rates. All default to 0, so a hospital that does not charge for ambulance
    # runs bills nothing and no invoice line is ever produced.
    base_charge = models.DecimalField(max_digits=10, decimal_places=2,
                                      default=Decimal('0.00'),
                                      help_text='Flat call-out charge')
    per_km_charge = models.DecimalField(max_digits=10, decimal_places=2,
                                        default=Decimal('0.00'))
    waiting_charge_per_hour = models.DecimalField(max_digits=10, decimal_places=2,
                                                  default=Decimal('0.00'))
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text='Your own cost per trip (fuel + wear), if you track it. '
                  'Leave 0 and the profit report says "not recorded" rather '
                  'than reporting 100% margin.')

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='ambulances')

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('registration_no',)
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'registration_no'],
                                    name='uniq_ambulance_reg_per_hospital'),
        ]

    def __str__(self):
        return f'{self.label or self.vehicle_type or "Ambulance"} · {self.registration_no}'

    @property
    def is_free(self):
        return self.is_active and self.status == self.STATUS_AVAILABLE


class AmbulanceTrip(models.Model):
    """One run: called → dispatched → completed, and what it cost."""

    TYPE_CHOICES = [
        ('EMERGENCY', 'Emergency pick-up'),
        ('TRANSFER', 'Transfer to another facility'),
        ('DISCHARGE', 'Discharge / drop home'),
        ('BODY', 'Body transfer'),
        ('OTHER', 'Other'),
    ]
    STATUS_REQUESTED = 'REQUESTED'
    STATUS_DISPATCHED = 'DISPATCHED'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (STATUS_REQUESTED, 'Requested'),
        (STATUS_DISPATCHED, 'Dispatched'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]
    # Statuses in which the vehicle is still out. Used by the board and by
    # `services.complete_trip` to decide when to hand the vehicle back.
    OPEN_STATUSES = (STATUS_REQUESTED, STATUS_DISPATCHED)

    ambulance = models.ForeignKey(Ambulance, on_delete=models.PROTECT, related_name='trips')
    driver = models.ForeignKey(AmbulanceDriver, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='trips')
    trip_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default='EMERGENCY')

    # A trip need not carry a registered patient — see the module docstring.
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='ambulance_trips')
    contact_name = models.CharField(max_length=120, blank=True,
                                    help_text='Who called, when there is no patient record')
    contact_phone = models.CharField(max_length=20, blank=True, db_index=True)

    from_location = models.CharField(max_length=200)
    to_location = models.CharField(max_length=200)
    distance_km = models.DecimalField(max_digits=7, decimal_places=1, default=Decimal('0.0'))
    waiting_hours = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))

    called_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_REQUESTED)

    # Frozen at creation from the vehicle's rates — see the module docstring.
    base_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    per_km_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    waiting_charge_per_hour = models.DecimalField(max_digits=10, decimal_places=2,
                                                  default=Decimal('0.00'))
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='ambulance_trips')

    notes = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='ambulance_trips')

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('-called_at', '-id')

    def __str__(self):
        return f'{self.who} · {self.from_location} → {self.to_location}'

    # -------------------------------------------------------------- display

    @property
    def who(self):
        """Whoever this trip was for — a patient, a caller, or nobody named."""
        if self.patient_id:
            return self.patient.full_name
        return self.contact_name or 'Unnamed call'

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    # --------------------------------------------------------------- money

    @property
    def distance_charge(self):
        return (self.per_km_charge or Decimal('0.00')) * (self.distance_km or Decimal('0.0'))

    @property
    def waiting_charge(self):
        return (self.waiting_charge_per_hour or Decimal('0.00')) * (self.waiting_hours or Decimal('0.0'))

    @property
    def total_charge(self):
        return (self.base_charge or Decimal('0.00')) + self.distance_charge + self.waiting_charge

    def charge_lines(self):
        """The bill lines this trip produces. A zero part produces no line, so a
        hospital that charges one flat rate bills exactly one line.

        The descriptions are what `billing.revenue` classifies on — change one
        here and change `revenue._PREFIXES` with it.
        """
        lines = []
        if self.base_charge:
            lines.append((f'Ambulance: {self.get_trip_type_display()}', self.base_charge))
        if self.distance_charge:
            lines.append((f'Ambulance Distance: {self.distance_km} km', self.distance_charge))
        if self.waiting_charge:
            lines.append((f'Ambulance Waiting: {self.waiting_hours} hr', self.waiting_charge))
        return lines
