"""Blood bank — donors, a unit (bag) inventory, and issue to a patient.

A `BloodUnit` is one physical bag. Its life is a status machine: AVAILABLE →
RESERVED / ISSUED, or EXPIRED / DISCARDED. `sellable`/`is_expired` are derived
from `expiry_date` so an out-of-date bag is never offered even if its status
still reads AVAILABLE (the daily reality — nobody re-flags every bag at midnight).
Issuing a unit to a patient stamps it ISSUED; nothing here bills — a transfusion
charge, if any, is raised through the normal service-invoice path.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from patients.models import Patient
from saas.utils import TenantManager

BLOOD_GROUPS = [(g, g) for g in ('A+', 'A-', 'B+', 'B-', 'O+', 'O-', 'AB+', 'AB-')]

COMPONENTS = [
    ('WHOLE', 'Whole Blood'),
    ('PRBC', 'Packed Red Cells'),
    ('FFP', 'Fresh Frozen Plasma'),
    ('PLT', 'Platelets'),
    ('CRYO', 'Cryoprecipitate'),
]


class BloodDonor(models.Model):
    full_name = models.CharField(max_length=150)
    cnic = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gender = models.CharField(max_length=1, choices=[('M', 'Male'), ('F', 'Female')], blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUPS)
    last_donation_date = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('full_name',)

    def __str__(self):
        return f"{self.full_name} ({self.blood_group})"


class BloodUnit(models.Model):
    STATUS = [
        ('AVAILABLE', 'Available'),
        ('RESERVED', 'Reserved'),
        ('ISSUED', 'Issued'),
        ('EXPIRED', 'Expired'),
        ('DISCARDED', 'Discarded'),
    ]
    bag_number = models.CharField(max_length=40)
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUPS)
    component = models.CharField(max_length=6, choices=COMPONENTS, default='WHOLE')
    donor = models.ForeignKey(BloodDonor, on_delete=models.SET_NULL, null=True, blank=True, related_name='units')
    donor_name = models.CharField(max_length=150, blank=True, help_text='If donor not on file')
    donation_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField()
    volume_ml = models.PositiveIntegerField(default=450)
    status = models.CharField(max_length=10, choices=STATUS, default='AVAILABLE')
    screening_done = models.BooleanField('Screened (HBV/HCV/HIV/etc.)', default=False)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('expiry_date', 'blood_group')

    def __str__(self):
        return f"{self.bag_number} — {self.blood_group} {self.get_component_display()}"

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def is_available(self):
        return self.status == 'AVAILABLE' and not self.is_expired


class BloodIssue(models.Model):
    unit = models.ForeignKey(BloodUnit, on_delete=models.PROTECT, related_name='issues')
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='blood_issues')
    issued_on = models.DateField(default=timezone.localdate)
    cross_match = models.CharField(max_length=120, blank=True, help_text='Compatible / result')
    notes = models.CharField(max_length=255, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-issued_on', '-id')

    def __str__(self):
        return f"{self.unit.bag_number} → {self.patient.full_name}"
