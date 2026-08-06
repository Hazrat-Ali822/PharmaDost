"""Consent forms — a template library + the signed record for a patient.

`ConsentTemplate` is the reusable wording (surgery, anaesthesia, blood
transfusion, etc.), per-tenant so a hospital keeps its own approved text.
`ConsentForm` is one filled-and-signed instance: the body text is **copied onto
the record** at creation, never referenced live, so editing a template later can
never rewrite what a patient already signed. The printable page carries signature
lines for the patient/guardian, the doctor and a witness.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager

CONSENT_TYPES = [
    ('SURGERY', 'Surgery / Operation'),
    ('ANAESTHESIA', 'Anaesthesia'),
    ('PROCEDURE', 'Procedure / Investigation'),
    ('BLOOD', 'Blood Transfusion'),
    ('HIV', 'HIV Testing'),
    ('GENERAL', 'General / Admission'),
    ('MLC', 'Medico-legal'),
]


class ConsentTemplate(models.Model):
    title = models.CharField(max_length=150)
    consent_type = models.CharField(max_length=12, choices=CONSENT_TYPES, default='GENERAL')
    body = models.TextField(help_text='Consent wording. Shown on the printed form for signature.')
    is_active = models.BooleanField(default=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('consent_type', 'title')

    def __str__(self):
        return self.title


class ConsentForm(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='consents')
    template = models.ForeignKey(ConsentTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    consent_type = models.CharField(max_length=12, choices=CONSENT_TYPES, default='GENERAL')
    title = models.CharField(max_length=150)
    body = models.TextField()                                    # frozen copy at signing time
    procedure_name = models.CharField(max_length=200, blank=True)
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True)
    signed_by = models.CharField(max_length=150, blank=True, help_text='Patient, or guardian if unable')
    relation = models.CharField(max_length=60, blank=True, help_text='Relation to patient, if a guardian signs')
    witness_name = models.CharField(max_length=150, blank=True)
    signed_on = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-signed_on', '-id')

    def __str__(self):
        return f"{self.title} — {self.patient.full_name}"
