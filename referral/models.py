"""Patient referrals (in / out) + a printable referral letter.

A referral records that a patient is being sent to another facility (OUT) — the
common case, and what the printed letter is for — or that they arrived on a
referral from elsewhere (IN). `facility` is always the *other* facility; the
letterhead names this hospital. The record is tenant-scoped; nothing here is
global.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager


class Referral(models.Model):
    DIRECTION = [('OUT', 'Referred Out'), ('IN', 'Referred In')]
    URGENCY = [('ROUTINE', 'Routine'), ('URGENT', 'Urgent'), ('EMERGENCY', 'Emergency')]
    STATUS = [('PENDING', 'Pending'), ('ACCEPTED', 'Accepted'),
              ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='referrals')
    direction = models.CharField(max_length=3, choices=DIRECTION, default='OUT')
    facility = models.CharField(max_length=200, help_text='The other facility (sent to / received from)')
    department = models.CharField(max_length=120, blank=True)
    referring_doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='referrals_made')
    receiving_doctor = models.CharField(max_length=150, blank=True)
    reason = models.CharField(max_length=255)
    clinical_summary = models.TextField(blank=True)
    investigations = models.TextField(blank=True)
    treatment_given = models.TextField(blank=True)
    urgency = models.CharField(max_length=10, choices=URGENCY, default='ROUTINE')
    status = models.CharField(max_length=10, choices=STATUS, default='PENDING')
    referral_date = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-referral_date', '-id')

    def __str__(self):
        arrow = '→' if self.direction == 'OUT' else '←'
        return f"{self.patient.full_name} {arrow} {self.facility}"

    @property
    def is_out(self):
        return self.direction == 'OUT'
