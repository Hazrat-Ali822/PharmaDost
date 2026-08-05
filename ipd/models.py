from decimal import Decimal

from django.db import models
from django.utils import timezone
from saas.utils import TenantManager

# The standard three-shift ward day. Times follow the common Pakistani pattern and
# are advisory (shown on screen); the roster keys on the shift name, not the clock.
SHIFT_CHOICES = [
    ('MORNING', 'Morning'),
    ('EVENING', 'Evening'),
    ('NIGHT', 'Night'),
]
SHIFT_TIMES = {
    'MORNING': '07:00 – 14:00',
    'EVENING': '14:00 – 21:00',
    'NIGHT': '21:00 – 07:00',
}


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
    # The senior nurse (Ward In-charge / Charge Nurse) who runs this ward — makes
    # the duty roster and allocates patients. Informational + shown on the board.
    in_charge = models.ForeignKey('accounts.User', on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='wards_in_charge',
                                  help_text='Ward In-charge (senior nurse who runs the ward)')
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

    @property
    def active_admission(self):
        return self.admissions.filter(status='Admitted').first()

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
    # The bill raised at discharge, kept so the printable discharge summary can
    # show its line items and the balance without re-deriving them.
    discharge_invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='+')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    @property
    def days_stayed(self):
        """Calendar days the bed was held, admission and discharge inclusive —
        the same count the bill charges for. Uses today when still admitted."""
        end = (self.discharge_date or timezone.now()).date()
        return max((end - self.admission_date.date()).days + 1, 1)

    def __str__(self):
        return f"Admission: {self.patient.full_name} ({self.status})"

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
        return f"Round: {self.admission.patient.full_name} at {self.round_time.strftime('%Y-%m-%d %H:%M')}"

class MedicationLog(models.Model):
    """One administered dose on the nursing chart.

    When the drug comes from the pharmacy catalogue (`medicine` set), giving it
    also moves stock and accrues a charge that lands on the discharge bill — so
    ward medication is neither invisible to inventory nor free to the patient.
    `medicine_name` stays authoritative for what was actually given: a ward may
    administer something off-catalogue, and that must remain recordable.

    `source` decides whether money and stock move at all. A patient often has
    their own supply at the bedside — bought outside, or brought from home — and
    the nurse is simply administering it. That dose belongs on the chart, but the
    pharmacy never issued it, so it must not reduce stock or reach the bill.
    """
    SOURCE_CHOICES = [
        ('PHARMACY', 'Hospital pharmacy stock'),
        ('PATIENT', "Patient's own supply (brought from outside)"),
    ]

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='medication_logs')
    medicine = models.ForeignKey('inventory.Medicine', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='ward_administrations',
                                 help_text="Pharmacy catalogue item, when the drug came from stock")
    medicine_name = models.CharField(max_length=150, verbose_name="Medicine Name")
    dosage = models.CharField(max_length=100, help_text="e.g. 500mg, 1 tablet, 2 puffs")
    quantity = models.PositiveIntegerField(default=1,
                                           help_text="Units given (tablets, vials, bottles)")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='PHARMACY',
                              verbose_name="Where the medicine came from")
    # Frozen at administration: the catalogue price may change before discharge,
    # and the patient must be billed what it cost on the day it was given.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    administered_at = models.DateTimeField(default=timezone.now, verbose_name="Administered Date & Time")
    administered_by = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='medications_administered')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    @property
    def charge(self):
        """What this dose adds to the patient's bill."""
        return (self.unit_price or Decimal('0.00')) * self.quantity

    def __str__(self):
        return f"{self.medicine_name} ({self.dosage}) to {self.admission.patient.full_name} by {self.administered_by.email}"

class AdmissionRequest(models.Model):
    """A doctor's advice that a patient should be admitted. Lands in the reception /
    ward-desk queue; on confirmation it becomes a real Admission (bed allocated)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Admitted', 'Admitted'),
        ('Cancelled', 'Cancelled'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='admission_requests')
    advised_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='admission_advices')
    reason = models.TextField(help_text='Why the patient needs admission')
    preferred_ward = models.ForeignKey(Ward, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    admission = models.ForeignKey(Admission, on_delete=models.SET_NULL, null=True, blank=True, related_name='from_request')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Admission advice: {self.patient.full_name} ({self.status})"


class NurseShift(models.Model):
    """One line of the duty roster: a nurse on duty in a ward for a date + shift.

    This is the roster the Ward In-charge builds. A ward has many nurses per shift;
    a nurse works one place per shift (the unique constraint). `duty` marks who is
    the shift in-charge — the senior nurse running that shift on the floor.
    """
    DUTY_CHOICES = [
        ('INCHARGE', 'Shift In-charge'),
        ('STAFF', 'Staff Nurse'),
    ]
    nurse = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='nurse_shifts')
    ward = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='shifts')
    date = models.DateField(default=timezone.localdate)
    shift = models.CharField(max_length=10, choices=SHIFT_CHOICES)
    duty = models.CharField(max_length=10, choices=DUTY_CHOICES, default='STAFF')
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-date', 'shift')
        constraints = [
            models.UniqueConstraint(fields=['nurse', 'date', 'shift'],
                                    name='uniq_nurse_date_shift'),
        ]

    @property
    def shift_time(self):
        return SHIFT_TIMES.get(self.shift, '')

    def __str__(self):
        return f"{self.nurse.get_full_name() or self.nurse.email} — {self.ward.name} {self.date} {self.get_shift_display()}"


class PatientAllocation(models.Model):
    """Which nurse is responsible for which inpatient, for a date + shift.

    The In-charge allocates the ward's admitted patients among the nurses rostered
    for that shift. The nurse-to-patient *ratio* is simply how many allocations a
    nurse holds in a shift (ICU 1:1–1:2, general 1:6–1:10). One patient has one
    responsible nurse per shift — the unique constraint.
    """
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='allocations')
    nurse = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='patient_allocations')
    date = models.DateField(default=timezone.localdate)
    shift = models.CharField(max_length=10, choices=SHIFT_CHOICES)
    assigned_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-date', 'shift')
        constraints = [
            models.UniqueConstraint(fields=['admission', 'date', 'shift'],
                                    name='uniq_admission_date_shift'),
        ]

    def __str__(self):
        return f"{self.admission.patient.full_name} → {self.nurse.get_full_name() or self.nurse.email} ({self.date} {self.get_shift_display()})"
