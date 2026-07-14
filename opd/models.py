from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from accounts.models import User
from patients.models import Patient


class Doctor(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=255, blank=True)
    pmdc_no = models.CharField(max_length=50, blank=True)
    opd_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    followup_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    followup_valid_days = models.PositiveIntegerField(default=7)
    share_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('100'),
        help_text="Doctor's share of the consultation fee (%). 100 = keeps the full fee.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class DoctorPayout(models.Model):
    """A payment made to a doctor against their earned consultation share."""
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='payouts')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_method = models.CharField(max_length=20, default='CASH')
    note = models.CharField(max_length=255, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='doctor_payouts')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-date', '-created_at')

    def __str__(self):
        return f"{self.doctor} — {self.amount}"


class Appointment(models.Model):
    STATUS_CHOICES = (
        ('BOOKED', 'Booked'),
        ('ARRIVED', 'Arrived'),
        ('IN_CONSULT', 'In Consult'),
        ('DONE', 'Done'),
        ('CANCELLED', 'Cancelled'),
    )
    VISIT_CHOICES = (
        ('OPD', 'OPD'),
        ('FOLLOWUP', 'Follow-up'),
        ('EMERGENCY', 'Emergency'),
    )

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='appointments')
    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name='appointments')
    appointment_date = models.DateField(default=timezone.localdate)
    slot_time = models.TimeField(null=True, blank=True)
    token_no = models.PositiveIntegerField(default=1)
    visit_type = models.CharField(max_length=12, choices=VISIT_CHOICES, default='OPD')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='BOOKED')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('appointment_date', 'token_no')

    def save(self, *args, **kwargs):
        if self._state.adding:
            last_token = (
                Appointment.objects.filter(
                    doctor=self.doctor,
                    appointment_date=self.appointment_date,
                ).aggregate(models.Max('token_no'))['token_no__max'] or 0
            )
            self.token_no = last_token + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.patient} - {self.doctor}"


class ClinicalRecord(models.Model):
    """A single clinical event in a patient's history — consultation, procedure,
    operation, ultrasound, x-ray, etc. — captured with full details."""
    TYPE_CHOICES = (
        ('CONSULT', 'Consultation / Diagnosis'),
        ('PROCEDURE', 'Procedure'),
        ('OPERATION', 'Operation / Surgery'),
        ('ULTRASOUND', 'Ultrasound'),
        ('XRAY', 'X-Ray'),
        ('ECG', 'ECG / Echo'),
        ('VACCINE', 'Vaccination'),
        ('OTHER', 'Other'),
    )
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='clinical_records')
    appointment = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='clinical_records')
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='clinical_records')
    record_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default='CONSULT')
    date = models.DateField(default=timezone.localdate)
    title = models.CharField(max_length=200, help_text='e.g. "Appendectomy", "Abdominal Ultrasound"')
    diagnosis = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True, help_text='Findings, operation notes, procedure details…')
    # simple vitals (optional)
    bp = models.CharField(max_length=20, blank=True, verbose_name='Blood Pressure')
    pulse = models.CharField(max_length=20, blank=True)
    temperature = models.CharField(max_length=20, blank=True)
    weight = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-date', '-created_at')

    def __str__(self):
        return f"{self.get_record_type_display()} — {self.title} ({self.patient})"
