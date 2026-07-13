from django.db import models
from django.utils import timezone
from opd.models import Appointment
from inventory.models import Medicine


class Prescription(models.Model):
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='prescriptions')
    complaint = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Prescription #{self.pk}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    dosage = models.CharField(max_length=50)
    duration_days = models.PositiveIntegerField(default=1)
    instructions = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.medicine.name} - {self.dosage}"