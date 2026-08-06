"""Birth & Death certificates — the hospital records office.

Both are stand-alone documents: a birth certificate can be issued for a delivery
that happened here (or is being registered later), and a death certificate for an
in-patient death or a body brought in. Neither requires a `Patient` row — the
newborn usually has none, and a death may be certified for someone never admitted —
so identity is captured as plain fields. `serial_no` is a per-hospital human
counter allocated in `save()` (like MRN / invoice numbers), independent per kind.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager, get_current_hospital


def _next_serial(model, hospital, prefix):
    """Per-hospital sequential serial for a certificate kind."""
    qs = model.all_objects.filter(hospital=hospital) if hospital else model.all_objects.filter(hospital__isnull=True)
    last = qs.count()
    return f"{prefix}-{last + 1:05d}"


class BirthCertificate(models.Model):
    SEX = [('M', 'Male'), ('F', 'Female'), ('A', 'Ambiguous')]

    serial_no = models.CharField(max_length=20, blank=True, db_index=True)
    child_name = models.CharField(max_length=150, blank=True, help_text='Leave blank if not yet named')
    sex = models.CharField(max_length=1, choices=SEX)
    date_of_birth = models.DateField()
    time_of_birth = models.TimeField(null=True, blank=True)
    place_of_birth = models.CharField(max_length=200, blank=True)
    weight_kg = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    mother_name = models.CharField(max_length=150)
    mother_cnic = models.CharField(max_length=20, blank=True)
    father_name = models.CharField(max_length=150, blank=True)
    father_cnic = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)

    registered_on = models.DateField(default=timezone.localdate)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('-registered_on', '-id')

    def save(self, *args, **kwargs):
        if not self.serial_no:
            hospital = self.hospital or get_current_hospital()
            self.serial_no = _next_serial(BirthCertificate, hospital, 'B')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.serial_no} — {self.child_name or 'B/o ' + self.mother_name}"


class DeathCertificate(models.Model):
    SEX = [('M', 'Male'), ('F', 'Female')]

    serial_no = models.CharField(max_length=20, blank=True, db_index=True)
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='death_certificates')
    deceased_name = models.CharField(max_length=150)
    sex = models.CharField(max_length=1, choices=SEX)
    age_text = models.CharField(max_length=40, blank=True, help_text='e.g. 62 years, 3 days')
    cnic = models.CharField(max_length=20, blank=True)
    father_husband_name = models.CharField(max_length=150, blank=True)

    date_of_death = models.DateField()
    time_of_death = models.TimeField(null=True, blank=True)
    place_of_death = models.CharField(max_length=200, blank=True)
    cause_of_death = models.CharField(max_length=255)
    secondary_causes = models.TextField(blank=True)
    manner = models.CharField(max_length=120, blank=True, help_text='Natural / accident / etc.')
    is_mlc = models.BooleanField('Medico-legal case', default=False)

    attending_doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    next_of_kin = models.CharField(max_length=150, blank=True)
    next_of_kin_cnic = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)

    registered_on = models.DateField(default=timezone.localdate)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='death_certs_registered')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('-registered_on', '-id')

    def save(self, *args, **kwargs):
        if not self.serial_no:
            hospital = self.hospital or get_current_hospital()
            self.serial_no = _next_serial(DeathCertificate, hospital, 'D')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.serial_no} — {self.deceased_name}"
