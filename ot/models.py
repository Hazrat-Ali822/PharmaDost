from django.db import models
from django.utils import timezone
from saas.utils import TenantManager

class SurgeryCategory(models.Model):
    name = models.CharField(max_length=100)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Surgery Categories"

class SurgeryProcedure(models.Model):
    name = models.CharField(max_length=150)
    category = models.ForeignKey(SurgeryCategory, on_delete=models.CASCADE, related_name='procedures')
    standard_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"{self.name} (Rs {self.standard_charge})"

class SurgeryRecord(models.Model):
    OUTCOME_CHOICES = [
        ('Successful', 'Successful'),
        ('Complications', 'Complications'),
        ('Failed', 'Failed'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='surgeries')
    admission = models.ForeignKey('ipd.Admission', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgeries')
    procedure = models.ForeignKey(SurgeryProcedure, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    lead_surgeon = models.ForeignKey('opd.Doctor', on_delete=models.CASCADE, related_name='surgeries_led')
    surgical_team = models.TextField(help_text="List other doctors, assistants, anesthetist, nurses etc.", blank=True)
    anesthesia_type = models.CharField(max_length=100, blank=True)
    operation_notes = models.TextField()
    outcome = models.CharField(max_length=50, choices=OUTCOME_CHOICES, default='Successful')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"Surgery: {self.procedure.name} - {self.patient.full_name}"

class SurgeryRequest(models.Model):
    """A doctor's advice that a patient needs surgery. Lands in the OT / reception
    queue; on scheduling it becomes a real SurgeryRecord (bill raised)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Scheduled', 'Scheduled'),
        ('Cancelled', 'Cancelled'),
    ]
    URGENCY_CHOICES = [
        ('Elective', 'Elective (planned)'),
        ('Urgent', 'Urgent'),
        ('Emergency', 'Emergency'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='surgery_requests')
    advised_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_advices')
    procedure = models.ForeignKey(SurgeryProcedure, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.TextField(help_text='Indication / reason for surgery')
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default='Elective')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    surgery = models.ForeignKey(SurgeryRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name='from_request')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Surgery advice: {self.patient.full_name} ({self.status})"
