"""ICD-10 diagnosis coding.

`DiagnosisCode` is the ICD-10 catalogue — a **global** reference table (codes are an
international standard, not per-tenant), seeded by `seed_icd` and extendable by an
admin. `PatientDiagnosis` is the tenant-scoped clinical record: a coded diagnosis
attached to a patient, replacing/augmenting the free-text `Appointment.diagnosis`
with something reportable and claim-ready.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager


class DiagnosisCode(models.Model):
    code = models.CharField(max_length=10, unique=True, db_index=True)   # e.g. J18.9
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=100, blank=True)              # ICD chapter/group
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ('code',)

    def __str__(self):
        return f"{self.code} — {self.title}"


class PatientDiagnosis(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='diagnoses')
    code = models.ForeignKey(DiagnosisCode, on_delete=models.PROTECT, related_name='patient_diagnoses')
    clinical_note = models.CharField(max_length=255, blank=True)
    diagnosed_on = models.DateField(default=timezone.localdate)
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-diagnosed_on', '-id')

    def __str__(self):
        return f"{self.patient.full_name}: {self.code.code}"
