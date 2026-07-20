from django.db import models
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
