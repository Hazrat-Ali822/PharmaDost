from django.db import models
from django.utils import timezone
from opd.models import Appointment
from inventory.models import Medicine


class Prescription(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('DISPENSED', 'Dispensed'),
    )
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='prescriptions')
    complaint = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Prescription #{self.pk}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.SET_NULL, null=True, blank=True)
    custom_medicine_name = models.CharField(max_length=255, blank=True)
    dosage = models.CharField(max_length=50)
    duration_days = models.PositiveIntegerField(default=1)
    instructions = models.CharField(max_length=255, blank=True)

    def __str__(self):
        if self.medicine:
            return f"{self.medicine.name} - {self.dosage}"
        return f"{self.custom_medicine_name} - {self.dosage}"


class RxPreset(models.Model):
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class RxPresetItem(models.Model):
    preset = models.ForeignKey(RxPreset, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE)
    dosage = models.CharField(max_length=50)
    duration_days = models.PositiveIntegerField(default=3)
    instructions = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.medicine.name} in {self.preset.name}"