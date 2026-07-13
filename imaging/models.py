from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from patients.models import Patient


class ScanType(models.Model):
    """A priced catalog entry for imaging services (e.g. 'Abdominal Ultrasound' Rs 1500).
    Admin manages these; doctors pick from them when ordering a scan."""
    MODALITY_CHOICES = (
        ("ULTRASOUND", "Ultrasound"),
        ("XRAY", "X-Ray"),
        ("CT", "CT Scan"),
        ("MRI", "MRI"),
        ("ECG", "ECG"),
        ("ECHO", "Echocardiography"),
        ("MAMMO", "Mammography"),
        ("OTHER", "Other"),
    )
    modality = models.CharField(max_length=20, choices=MODALITY_CHOICES, default="ULTRASOUND")
    name = models.CharField(max_length=150, help_text="e.g. Abdominal Ultrasound, Chest X-Ray (PA)")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("modality", "name")

    def __str__(self):
        return f"{self.get_modality_display()} — {self.name}"


class ImagingStudy(models.Model):
    """One radiology / imaging study for a patient (ultrasound, x-ray, CT, MRI,
    ECG...). Mirrors the lab order->result flow but a single study == a single
    report (findings + impression + film), which fits how radiology works.
    """

    MODALITY_CHOICES = (
        ("ULTRASOUND", "Ultrasound"),
        ("XRAY", "X-Ray"),
        ("CT", "CT Scan"),
        ("MRI", "MRI"),
        ("ECG", "ECG"),
        ("ECHO", "Echocardiography"),
        ("MAMMO", "Mammography"),
        ("OTHER", "Other"),
    )
    STATUS_CHOICES = (
        ("Pending", "Pending"),          # ordered, not yet performed
        ("In Progress", "In Progress"),  # patient in the scan room
        ("Reported", "Reported"),        # findings + impression written
        ("Delivered", "Delivered"),      # report handed to patient
    )

    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="imaging_studies"
    )
    modality = models.CharField(
        max_length=20, choices=MODALITY_CHOICES, default="ULTRASOUND"
    )
    study_name = models.CharField(
        max_length=150,
        help_text="e.g. Abdominal Ultrasound, Chest X-Ray (PA view)",
    )
    clinical_note = models.TextField(
        blank=True, help_text="Referring clinical note / reason for the scan"
    )
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imaging_referred",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imaging_performed",
    )
    study_date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # the report
    findings = models.TextField(blank=True)
    impression = models.TextField(blank=True)
    image = models.ImageField(upload_to="imaging/", blank=True, null=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-study_date"]
        verbose_name = "Imaging Study"
        verbose_name_plural = "Imaging Studies"

    def __str__(self):
        return f"{self.get_modality_display()} #{self.id} - {self.patient.full_name}"

    @property
    def is_reported(self):
        return bool((self.findings or "").strip() or (self.impression or "").strip())
