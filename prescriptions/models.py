from django.conf import settings
from django.db import models
from django.utils import timezone
from opd.models import Appointment
from inventory.models import Medicine
from saas.utils import TenantManager


class Prescription(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('PARTIAL', 'Partially Dispensed'),
        ('DISPENSED', 'Dispensed'),
        # Patient declined the medicines, or the doctor withdrew the Rx. Nothing is
        # deleted; the Rx just stops sitting in the pharmacy's pending queue for ever.
        ('CANCELLED', 'Cancelled'),
    )
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='prescriptions')
    complaint = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(default=timezone.now)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_prescriptions')
    cancel_reason = models.CharField(max_length=255, blank=True)

    @property
    def active_items(self):
        """The medicines still to be dispensed — what the POS should pre-load."""
        return self.items.filter(is_cancelled=False)

    @property
    def is_cancelled(self):
        return self.status == 'CANCELLED'

    def __str__(self):
        return f"Prescription #{self.pk}"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.SET_NULL, null=True, blank=True)
    custom_medicine_name = models.CharField(max_length=255, blank=True)
    dosage = models.CharField(max_length=50)
    duration_days = models.PositiveIntegerField(default=1)
    instructions = models.CharField(max_length=255, blank=True)

    # A medicine the patient declined at the counter. The pharmacist marks it here
    # rather than the line being deleted, so the doctor's original Rx stays intact
    # and the printed sheet can show "Cancelled — patient refused".
    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_rx_items')
    cancel_reason = models.CharField(max_length=255, blank=True)

    @property
    def display_name(self):
        return self.medicine.name if self.medicine else self.custom_medicine_name

    def __str__(self):
        if self.medicine:
            return f"{self.medicine.name} - {self.dosage}"
        return f"{self.custom_medicine_name} - {self.dosage}"


class RxPreset(models.Model):
    # Nullable and tenant-managed, like every other model with this column.
    # It was NOT NULL and manager-less, so both creation paths carried
    # `preset.hospital = request.user.hospital or Hospital.objects.first()` —
    # and that fallback filed a hospital-less user's preset into whichever
    # tenant happened to have the lowest id, i.e. a real customer's data. On
    # the desktop/LAN build there are no Hospital rows at all, so `.first()`
    # was None and saving raised IntegrityError: the feature simply crashed.
    # `saas.signals.auto_assign_hospital` stamps this from the thread-local now,
    # exactly as it does everywhere else.
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True)
    doctor = models.ForeignKey('opd.Doctor', on_delete=models.CASCADE, null=True, blank=True, related_name='rx_presets')
    name = models.CharField(max_length=100)
    complaint = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    # Scoped like every other tenant model. `_scoped_presets` still adds the
    # per-doctor narrowing on top; this is the net underneath it.
    objects = TenantManager()
    all_objects = models.Manager()

    def __str__(self):
        return self.name


class RxPresetItem(models.Model):
    preset = models.ForeignKey(RxPreset, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.SET_NULL, null=True, blank=True)
    custom_medicine_name = models.CharField(max_length=255, blank=True)
    dosage = models.CharField(max_length=50)
    duration_days = models.PositiveIntegerField(default=3)
    instructions = models.CharField(max_length=255, blank=True)

    def __str__(self):
        name = self.medicine.name if self.medicine else self.custom_medicine_name
        return f"{name} in {self.preset.name}"