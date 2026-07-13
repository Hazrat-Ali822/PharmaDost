from django.db import models
from django.utils import timezone
from saas.utils import TenantManager

class Ward(models.Model):
    WARD_TYPE_CHOICES = [
        ('General Male', 'General Ward (Male)'),
        ('General Female', 'General Ward (Female)'),
        ('ICU', 'Intensive Care Unit (ICU)'),
        ('CCU', 'Coronary Care Unit (CCU)'),
        ('Paediatric', 'Paediatric (Kids) Ward'),
        ('Surgical', 'Surgical Ward'),
        ('Maternity', 'Maternity Ward'),
        ('Private Room', 'Private Room'),
        ('Semi-Private Room', 'Semi-Private Room'),
    ]
    name = models.CharField(max_length=100)
    ward_type = models.CharField(max_length=50, choices=WARD_TYPE_CHOICES, default='General Male')
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"{self.name} [{self.get_ward_type_display()}] (Rs {self.daily_rate}/day)"

class Bed(models.Model):
    STATUS_CHOICES = [
        ('Available', 'Available'),
        ('Occupied', 'Occupied'),
        ('Maintenance', 'Maintenance'),
    ]
    bed_number = models.CharField(max_length=50)
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='beds')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"Bed {self.bed_number} - {self.ward.name} ({self.status})"

class Admission(models.Model):
    STATUS_CHOICES = [
        ('Admitted', 'Admitted'),
        ('Discharged', 'Discharged'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='admissions')
    bed = models.ForeignKey(Bed, on_delete=models.CASCADE, related_name='admissions')
    admission_date = models.DateTimeField(default=timezone.now)
    discharge_date = models.DateTimeField(null=True, blank=True)
    admission_reason = models.TextField()
    attending_doctor = models.ForeignKey('opd.Doctor', on_delete=models.CASCADE, related_name='admissions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Admitted')
    discharge_notes = models.TextField(blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"Admission: {self.patient.name} ({self.status})"

class DoctorRound(models.Model):
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='rounds')
    round_time = models.DateTimeField(default=timezone.now)
    clinical_notes = models.TextField()
    prescription_updates = models.TextField(blank=True)
    vitals_temp = models.CharField(max_length=50, blank=True, verbose_name="Temperature (°F)")
    vitals_bp = models.CharField(max_length=50, blank=True, verbose_name="Blood Pressure")
    vitals_pulse = models.CharField(max_length=50, blank=True, verbose_name="Pulse Rate (bpm)")
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"Round: {self.admission.patient.name} at {self.round_time.strftime('%Y-%m-%d %H:%M')}"

class MedicationLog(models.Model):
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='medication_logs')
    medicine_name = models.CharField(max_length=150, verbose_name="Medicine Name")
    dosage = models.CharField(max_length=100, help_text="e.g. 500mg, 1 tablet, 2 puffs")
    administered_at = models.DateTimeField(default=timezone.now, verbose_name="Administered Date & Time")
    administered_by = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='medications_administered')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return f"{self.medicine_name} ({self.dosage}) to {self.admission.patient.full_name} by {self.administered_by.email}"
