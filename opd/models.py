from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from accounts.models import User
from patients.models import Patient
from saas.utils import TenantManager


def _clock(value):
    """'09:05' rather than '09:05 AM' padded — reception reads these at a glance."""
    return value.strftime('%I:%M %p').lstrip('0') if value else ''


class Department(models.Model):
    """An OPD department patients are sent to — Medicine, Gynae, Peads, ENT…

    Reception picks the department first and only that department's doctors are
    offered, which is how a real front desk works: the patient says what is wrong,
    not which doctor they want.
    """
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('name',)
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'name'], name='uniq_department_per_hospital'),
        ]

    def __str__(self):
        return self.name


class Doctor(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='doctors')
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

    def availability(self, at=None):
        """Is this doctor sitting right now, and what should reception be told?

        Two layers, in this order:

        1. An override for today — set with one click when the doctor is on leave
           or has come in off-schedule. Whatever it says, wins.
        2. The weekly OPD timings.

        Timings alone are not enough (a doctor who takes the day off still shows
        as available) and a manual switch alone is not enough (someone has to
        remember to flip it every morning). Together, the common case needs no
        daily effort and the exception is one click.

        Reads `schedules` / `availability_overrides` through `.all()` so a
        prefetch is used when the caller set one up — see `sitting_doctors`.
        """
        now = at or timezone.localtime()
        today, now_time = now.date(), now.time()

        for override in self.availability_overrides.all():
            if override.date != today:
                continue
            if override.available:
                return {'available': True, 'state': 'in',
                        'label': override.note or 'Available now'}
            return {'available': False, 'state': 'off',
                    'label': override.note or 'On leave today'}

        slots = sorted((s for s in self.schedules.all() if s.weekday == today.weekday()),
                       key=lambda s: s.start_time)
        for slot in slots:
            if slot.start_time <= now_time <= slot.end_time:
                return {'available': True, 'state': 'in',
                        'label': f'In OPD till {_clock(slot.end_time)}'}
        later = [s for s in slots if s.start_time > now_time]
        if later:
            return {'available': False, 'state': 'later',
                    'label': f'From {_clock(later[0].start_time)}'}
        if slots:
            return {'available': False, 'state': 'done', 'label': 'OPD finished for today'}
        return {'available': False, 'state': 'off', 'label': 'Not in OPD today'}

    @property
    def is_available_now(self):
        return self.availability()['available']

    @property
    def availability_label(self):
        return self.availability()['label']

    @property
    def timings_summary(self):
        """'Mon–Sat 9:00 AM–2:00 PM' style line for the doctor list and the slip."""
        slots = sorted(self.schedules.all(), key=lambda s: (s.weekday, s.start_time))
        if not slots:
            return ''
        return ', '.join(
            f"{s.get_weekday_display()[:3]} {_clock(s.start_time)}–{_clock(s.end_time)}"
            for s in slots)


class DoctorSchedule(models.Model):
    """One sitting: the hours a doctor is in OPD on a given weekday.

    A doctor can have more than one row per day (morning and evening OPD)."""
    WEEKDAYS = (
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    )

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='schedules')
    # matches date.weekday(): Monday is 0
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ('weekday', 'start_time')

    def __str__(self):
        return f"{self.doctor.full_name}: {self.get_weekday_display()} {_clock(self.start_time)}–{_clock(self.end_time)}"


class DoctorAvailabilityOverride(models.Model):
    """Beats the weekly timings for ONE date.

    `available=False` is 'on leave today'; `available=True` is 'came in even
    though today is not a scheduled day'. Stored per date rather than as a flag
    on the doctor so yesterday's leave does not silently hide them tomorrow.
    """
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE,
                              related_name='availability_overrides')
    date = models.DateField(default=timezone.localdate)
    available = models.BooleanField(default=False)
    note = models.CharField(max_length=120, blank=True,
                            help_text="Shown to reception, e.g. 'On leave — back Monday'")
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='doctor_availability_changes')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-date',)
        constraints = [
            models.UniqueConstraint(fields=['doctor', 'date'], name='uniq_doctor_override_per_day'),
        ]

    def __str__(self):
        state = 'available' if self.available else 'off'
        return f"{self.doctor.full_name} {state} on {self.date}"


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
