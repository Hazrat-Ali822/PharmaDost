"""Vaccination / EPI records.

`Vaccine` is the schedule catalogue — a **global** reference table (Pakistan's EPI
schedule is a national standard, not per-tenant), seeded by `seed_epi` and
extendable by an admin. `VaccinationRecord` is the tenant-scoped dose given to a
patient. `next_due_date` is stored (the vaccinator sets it when giving a dose)
rather than derived, because the interval to the next dose depends on the child's
actual visit, not an idealised calendar.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from patients.models import Patient
from saas.utils import TenantManager


class Vaccine(models.Model):
    code = models.CharField(max_length=20, unique=True, db_index=True)     # e.g. PENTA1
    name = models.CharField(max_length=120)                               # Pentavalent (1st dose)
    recommended_age = models.CharField(max_length=60, blank=True)         # "6 weeks"
    doses_in_series = models.PositiveIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=0)                     # schedule order
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('sequence', 'code')

    def __str__(self):
        return f"{self.code} — {self.name}"


class VaccinationRecord(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='vaccinations')
    vaccine = models.ForeignKey(Vaccine, on_delete=models.PROTECT, related_name='records')
    dose_number = models.PositiveIntegerField(default=1)
    date_given = models.DateField(default=timezone.localdate)
    batch_no = models.CharField(max_length=40, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    given_by = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-date_given', '-id')

    def __str__(self):
        return f"{self.patient.full_name}: {self.vaccine.code} (dose {self.dose_number})"
