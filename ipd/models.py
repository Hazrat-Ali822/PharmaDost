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

# How often a ward patient should have observations taken before they count as
# overdue on the nursing board. Advisory — a clinician sets the real frequency.
OBS_INTERVAL_HOURS = 6


def compute_mews(*, temperature_f=None, pulse=None, respiratory_rate=None,
                 systolic_bp=None, avpu='A'):
    """Modified Early Warning Score (MEWS).

    Each parameter scores 0–3; the total and the single highest score decide the
    escalation band. This is the ward's earliest signal that a patient is
    deteriorating — a rising score, not one bad reading, is what it catches.

    Temperature is taken in °F (what these wards use) and converted to °C for
    scoring. A missing parameter scores 0 but marks the set incomplete, so the
    board can show that obs were only partially taken.
    """
    def band_bp(v):
        if v is None: return None
        if v <= 70: return 3
        if v <= 80: return 2
        if v <= 100: return 1
        if v <= 199: return 0
        return 2

    def band_pulse(v):
        if v is None: return None
        if v <= 40: return 2
        if v <= 50: return 1
        if v <= 100: return 0
        if v <= 110: return 1
        if v <= 129: return 2
        return 3

    def band_rr(v):
        # NEWS2 respiratory-rate bands: a normal 12–20 scores 0.
        if v is None: return None
        if v <= 8: return 3
        if v <= 11: return 1
        if v <= 20: return 0
        if v <= 24: return 2
        return 3

    def band_temp_c(c):
        if c is None: return None
        if c < 35: return 2
        if c <= 38.4: return 0
        return 2

    temp_c = None
    if temperature_f not in (None, ''):
        try:
            temp_c = (float(temperature_f) - 32) * 5.0 / 9.0
        except (TypeError, ValueError):
            temp_c = None

    parts = {
        'bp': band_bp(systolic_bp),
        'pulse': band_pulse(pulse),
        'rr': band_rr(respiratory_rate),
        'temp': band_temp_c(temp_c),
        'avpu': {'A': 0, 'V': 1, 'P': 2, 'U': 3}.get(avpu or 'A', 0),
    }
    present = [v for v in parts.values() if v is not None]
    score = sum(present)
    highest = max(present) if present else 0
    # AVPU always has a value (defaults Alert); the four measured vitals decide completeness.
    complete = all(parts[k] is not None for k in ('bp', 'pulse', 'rr', 'temp'))

    if score >= 4 or highest >= 3:
        band, color, advice = 'RED', '#ef4444', 'Urgent — inform the doctor now and increase monitoring.'
    elif score >= 2:
        band, color, advice = 'AMBER', '#f59e0b', 'Increase observation frequency; inform the nurse in-charge.'
    else:
        band, color, advice = 'GREEN', '#22c55e', 'Routine monitoring.'
    return {'score': score, 'highest': highest, 'band': band, 'color': color,
            'advice': advice, 'complete': complete, 'parts': parts}


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


class VitalsObservation(models.Model):
    """One nursing observation set — the TPR/vitals chart, taken every few hours.

    Separate from `DoctorRound`: the nurse owns this chart, and the **MEWS** score
    computed off it (see `compute_mews`) is the ward's early-warning trigger.
    Respiratory rate, SpO2, pain and consciousness live here because they are the
    parameters a plain temp/pulse/BP row leaves out — and RR is the earliest sign
    of deterioration. Numeric fields are nullable so a partial set is still saveable
    (a dose already given, an obs half-taken), and MEWS marks it incomplete.
    """
    AVPU_CHOICES = [
        ('A', 'Alert'), ('V', 'Responds to voice'),
        ('P', 'Responds to pain'), ('U', 'Unresponsive'),
    ]
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='observations')
    taken_at = models.DateTimeField(default=timezone.now)
    taken_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='+')
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True,
                                      verbose_name='Temperature (°F)')
    pulse = models.PositiveIntegerField(null=True, blank=True, verbose_name='Pulse (bpm)')
    respiratory_rate = models.PositiveIntegerField(null=True, blank=True,
                                                   verbose_name='Respiratory rate (/min)')
    systolic_bp = models.PositiveIntegerField(null=True, blank=True, verbose_name='Systolic BP')
    diastolic_bp = models.PositiveIntegerField(null=True, blank=True, verbose_name='Diastolic BP')
    spo2 = models.PositiveIntegerField(null=True, blank=True, verbose_name='SpO₂ (%)')
    consciousness = models.CharField(max_length=1, choices=AVPU_CHOICES, default='A',
                                     verbose_name='Consciousness (AVPU)')
    pain_score = models.PositiveIntegerField(null=True, blank=True,
                                             verbose_name='Pain score (0–10)')
    blood_glucose = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                        verbose_name='Blood glucose (mg/dL)')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-taken_at',)

    @property
    def mews(self):
        return compute_mews(
            temperature_f=self.temperature, pulse=self.pulse,
            respiratory_rate=self.respiratory_rate, systolic_bp=self.systolic_bp,
            avpu=self.consciousness)

    @property
    def bp_display(self):
        if self.systolic_bp and self.diastolic_bp:
            return f"{self.systolic_bp}/{self.diastolic_bp}"
        return self.systolic_bp or '—'

    def __str__(self):
        return f"Obs {self.admission.patient.full_name} @ {self.taken_at:%Y-%m-%d %H:%M} (MEWS {self.mews['score']})"


class FluidBalanceEntry(models.Model):
    """One line of the intake–output chart. Fluids in (IV, oral, NG) and out
    (urine, drain, vomit) are logged in millilitres; the running balance per day
    is how overload and dehydration are managed on the ward."""
    DIRECTION_CHOICES = [('IN', 'Intake'), ('OUT', 'Output')]
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='fluid_entries')
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='+')
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    kind = models.CharField(max_length=40, help_text='e.g. IV fluid, Oral, NG feed, Urine, Drain, Vomit')
    volume_ml = models.PositiveIntegerField(verbose_name='Volume (mL)')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-recorded_at',)
        verbose_name_plural = 'Fluid balance entries'

    def __str__(self):
        return f"{self.get_direction_display()} {self.kind} {self.volume_ml}mL — {self.admission.patient.full_name}"


def fluid_totals(admission, on_date):
    """Intake, output and running balance (mL) for one admission on one date."""
    from django.db.models import Sum
    qs = FluidBalanceEntry.objects.filter(admission=admission, recorded_at__date=on_date)
    intake = qs.filter(direction='IN').aggregate(s=Sum('volume_ml'))['s'] or 0
    output = qs.filter(direction='OUT').aggregate(s=Sum('volume_ml'))['s'] or 0
    return {'intake': intake, 'output': output, 'balance': intake - output}


class NursingNote(models.Model):
    """A nurse's shift progress note — the narrative of what was observed and done.

    The nurse's own contemporaneous record, kept separate from `DoctorRound`: a
    ward note is a legal document in its own right, and folding it into the doctor's
    round would erase who wrote what.
    """
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='nursing_notes')
    noted_at = models.DateTimeField(default=timezone.now)
    noted_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='+')
    shift = models.CharField(max_length=10, choices=SHIFT_CHOICES, blank=True)
    note = models.TextField()
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-noted_at',)

    def __str__(self):
        return f"Note {self.admission.patient.full_name} @ {self.noted_at:%Y-%m-%d %H:%M}"


class ShiftHandover(models.Model):
    """End-of-shift **SBAR** handover for one patient, from the outgoing nurse to
    the next shift. Situation / Background / Assessment / Recommendation is the
    standard structure; the incoming nurse acknowledges it."""
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='handovers')
    date = models.DateField(default=timezone.localdate)
    shift = models.CharField(max_length=10, choices=SHIFT_CHOICES,
                             help_text='The shift being handed over')
    from_nurse = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+')
    situation = models.TextField(help_text='Why the patient is here / current problem')
    background = models.TextField(blank=True)
    assessment = models.TextField(blank=True, help_text='How they are now — obs, concerns')
    recommendation = models.TextField(blank=True, help_text='What the next shift must do')
    acknowledged_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                        blank=True, related_name='+')
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Handover {self.admission.patient.full_name} — {self.date} {self.get_shift_display()}"


class CareTask(models.Model):
    """A logged nursing care task — turning, hygiene, catheter care and the rest of
    the routine ward work that a vitals/med chart does not capture. A simple 'done
    at this time by this nurse' record; the ward's task chart."""
    TASK_CHOICES = [
        ('TURN', 'Positioning / turning'),
        ('HYGIENE', 'Hygiene / bed bath'),
        ('CATHETER', 'Catheter care'),
        ('IV', 'IV site care'),
        ('WOUND', 'Wound / dressing'),
        ('MOBILISE', 'Mobilisation'),
        ('FEED', 'Feeding / NG'),
        ('OTHER', 'Other'),
    ]
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name='care_tasks')
    task = models.CharField(max_length=12, choices=TASK_CHOICES)
    done_at = models.DateTimeField(default=timezone.now)
    done_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True,
                                blank=True, related_name='+')
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-done_at',)

    def __str__(self):
        return f"{self.get_task_display()} — {self.admission.patient.full_name} @ {self.done_at:%H:%M}"


def ward_census(admissions_qs, on_date):
    """A day's ward census from the admission records: how many were admitted and
    discharged on `on_date`, and the current occupancy. `admissions_qs` is an
    already-scoped Admission queryset (tenant + role)."""
    admitted_today = admissions_qs.filter(admission_date__date=on_date).count()
    discharged_today = admissions_qs.filter(discharge_date__date=on_date).count()
    currently_admitted = admissions_qs.filter(status='Admitted').count()
    return {
        'admitted_today': admitted_today,
        'discharged_today': discharged_today,
        'currently_admitted': currently_admitted,
    }
