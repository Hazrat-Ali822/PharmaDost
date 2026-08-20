"""Maternity / Obstetrics — antenatal care, deliveries and the birth register.

A big part of a Pakistani hospital's work. `Pregnancy` is the ANC record (one per
pregnancy), `AntenatalVisit` the checkup chart, `Delivery` the delivery event, and
`Birth` the register entry (one per baby, so twins are two rows). The expected date
of delivery and gestational age are derived from the LMP (Naegele's rule) and never
stored, so a corrected LMP re-derives them.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager


class Pregnancy(models.Model):
    STATUS_CHOICES = [
        ('ONGOING', 'Ongoing'),
        ('DELIVERED', 'Delivered'),
        ('CLOSED', 'Closed'),
    ]
    mother = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='pregnancies')
    husband_name = models.CharField(max_length=255, blank=True)
    lmp = models.DateField(null=True, blank=True, help_text='Last menstrual period')
    gravida = models.PositiveIntegerField(default=1)     # total pregnancies incl. this
    para = models.PositiveIntegerField(default=0)        # prior viable births
    abortions = models.PositiveIntegerField(default=0)
    blood_group = models.CharField(max_length=10, blank=True)
    high_risk = models.BooleanField(default=False)
    risk_notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ONGOING')
    registered_on = models.DateField(default=timezone.localdate)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                      null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-registered_on',)

    def __str__(self):
        return f"ANC — {self.mother.full_name}"

    @property
    def edd(self):
        """Expected date of delivery — Naegele's rule (LMP + 280 days)."""
        return self.lmp + timedelta(days=280) if self.lmp else None

    @property
    def gestation_weeks(self):
        if not self.lmp:
            return None
        return max(0, (timezone.localdate() - self.lmp).days // 7)


class AntenatalVisit(models.Model):
    pregnancy = models.ForeignKey(Pregnancy, on_delete=models.CASCADE, related_name='visits')
    date = models.DateField(default=timezone.localdate)
    weight = models.CharField(max_length=20, blank=True)
    bp = models.CharField(max_length=20, blank=True)
    fundal_height = models.CharField(max_length=20, blank=True)
    fhr = models.CharField(max_length=20, blank=True)            # fetal heart rate
    complaints = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    next_visit = models.DateField(null=True, blank=True)
    seen_by = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-date',)

    @property
    def weeks(self):
        lmp = self.pregnancy.lmp
        return max(0, (self.date - lmp).days // 7) if lmp else None


class Delivery(models.Model):
    TYPE_CHOICES = [
        ('NORMAL', 'Normal (SVD)'),
        ('CSECTION', 'C-Section'),
        ('ASSISTED', 'Assisted (forceps / vacuum)'),
        ('BREECH', 'Breech'),
    ]
    OUTCOME_CHOICES = [
        ('LIVE', 'Live birth'),
        ('STILL', 'Stillbirth'),
    ]
    pregnancy = models.ForeignKey(Pregnancy, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='deliveries')
    mother = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='deliveries')
    delivered_at = models.DateTimeField(default=timezone.now)
    delivery_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='NORMAL')
    outcome = models.CharField(max_length=6, choices=OUTCOME_CHOICES, default='LIVE')
    conducted_by = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    # Delivery kit, sutures, cord clamp, drugs given in the labour room.
    # Frozen here, not in a catalogue: a normal delivery and a complicated one
    # consume very different amounts. 0 = not recorded.
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-delivered_at',)

    def __str__(self):
        return f"Delivery — {self.mother.full_name} ({self.get_delivery_type_display()})"


class Birth(models.Model):
    SEX_CHOICES = [('M', 'Male'), ('F', 'Female'), ('A', 'Ambiguous')]
    STATUS_CHOICES = [('ALIVE', 'Alive'), ('DECEASED', 'Deceased')]

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='births')
    sex = models.CharField(max_length=1, choices=SEX_CHOICES, default='M')
    weight_kg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    birth_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='ALIVE')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"Baby ({self.get_sex_display()}) — {self.delivery.mother.full_name}"
