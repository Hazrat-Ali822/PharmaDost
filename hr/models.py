"""Staff HR — the people side of the hospital: who works here, their attendance,
leave, and monthly salary. Kept separate from `accounts.User` (which is only auth
+ role) so payroll data doesn't bloat the login model, and so a StaffProfile row is
optional — a user without one simply has no salary set yet.
"""
from datetime import time
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from saas.utils import TenantManager


# What a hospital gets on first use, and nothing more — it is a starting point to
# edit, not the shape every hospital must take.
DEFAULT_SHIFTS = (
    ('Morning', time(7, 0), time(14, 0)),
    ('Evening', time(14, 0), time(21, 0)),
    ('Night', time(21, 0), time(7, 0)),
)


class Shift(models.Model):
    """A working shift, defined by each hospital for itself.

    Three eight-hour shifts is the common pattern, but a single-doctor clinic runs
    one long day, a busy hospital splits four, night duty starts at 20:00 in one
    place and 22:00 in the next, and the names are as often "A / B / C" as
    morning/evening/night. This used to be a hardcoded three-entry list with fixed
    times that no hospital could change — hence a per-tenant table, the same shape
    as the lab and imaging catalogues.

    A shift may **cross midnight** (21:00 -> 07:00), and that is the case every
    naive implementation gets wrong. `covers()` is the only thing allowed to
    decide whether a shift is running: a plain `start <= t <= end` is never true
    for a night shift, so it would report "no shift is on" for the eight hours the
    ward is at its thinnest.
    """

    name = models.CharField(max_length=40, help_text='e.g. Morning, Night, Shift A')
    start_time = models.TimeField()
    end_time = models.TimeField(help_text='May be earlier than the start — a night '
                                          'shift crossing midnight is normal.')
    order = models.PositiveSmallIntegerField(
        default=0, help_text='Position in the roster grid (lowest first).')
    is_active = models.BooleanField(
        default=True,
        help_text='Retired shifts stay here so old rosters still read correctly.')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='shifts')

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('order', 'start_time')
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'name'],
                                    name='uniq_shift_name_per_hospital'),
        ]

    def __str__(self):
        return f'{self.name} ({self.time_range})'

    # ---------------------------------------------------------------- clock

    @property
    def time_range(self):
        return f'{self.start_time:%H:%M} – {self.end_time:%H:%M}'

    @property
    def crosses_midnight(self):
        return self.end_time <= self.start_time

    def covers(self, t):
        """Is `t` (a time) inside this shift?"""
        if self.crosses_midnight:
            return t >= self.start_time or t < self.end_time
        return self.start_time <= t < self.end_time

    @property
    def hours(self):
        s = self.start_time.hour * 60 + self.start_time.minute
        e = self.end_time.hour * 60 + self.end_time.minute
        if self.crosses_midnight:
            e += 24 * 60
        return round((e - s) / 60, 1)

    # ------------------------------------------------------------- lookups
    #
    # These go through `all_objects` keyed on the hospital *value* rather than
    # through `objects`, for the reason the lab/imaging catalogue editors do:
    # `TenantManager` lets a superuser past unfiltered, and the desktop/LAN
    # install is hospital-less with a superuser admin — it must still see its
    # own shifts and nobody else's.

    @staticmethod
    def _resolve(hospital=None):
        if hospital is not None:
            return hospital
        from saas.utils import get_current_hospital
        return get_current_hospital()

    @classmethod
    def ensure_defaults(cls, hospital=None):
        """Give a hospital that has never configured shifts the usual three.

        Called on first read so the roster is never an empty screen with no way
        forward. It only ever fires when the hospital has *no* shifts at all, so
        deleting one down to two does not resurrect the third.
        """
        h = cls._resolve(hospital)
        if cls.all_objects.filter(hospital=h).exists():
            return
        for i, (name, start, end) in enumerate(DEFAULT_SHIFTS):
            cls.all_objects.create(hospital=h, name=name, start_time=start,
                                   end_time=end, order=i)

    @classmethod
    def for_hospital(cls, hospital=None, include_inactive=False):
        h = cls._resolve(hospital)
        cls.ensure_defaults(h)
        qs = cls.all_objects.filter(hospital=h)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return qs.order_by('order', 'start_time')

    @classmethod
    def current(cls, hospital=None, at=None):
        """The shift running now, or None when the hospital has none active.

        Falls back to the first shift rather than nothing when the configured
        shifts leave a gap in the day — a roster screen with no shift selected is
        useless, and a gap is a configuration mistake, not a state to model.
        """
        now = (at or timezone.localtime()).time()
        shifts = list(cls.for_hospital(hospital))
        for s in shifts:
            if s.covers(now):
                return s
        return shifts[0] if shifts else None


class StaffProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='staff_profile')
    photo = models.ImageField(upload_to='staff_photos/', null=True, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    allowed_monthly_leaves = models.IntegerField(default=2, help_text="Monthly allowed paid leaves")
    enable_absence_deduction = models.BooleanField(default=True, help_text="Enable dynamic salary deduction for absences/excess leaves")
    deduction_per_absent_day = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Fixed daily deduction rate, or leave empty for auto (salary / 30)")
    joining_date = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    cnic = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=50, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return self.user.get_full_name() or self.user.email


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent'),
        ('LEAVE', 'Leave'),
        ('HALF', 'Half day'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='PRESENT')
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        # One attendance row per person per day — the grid upserts on this.
        unique_together = [('user', 'date')]
        ordering = ('-date',)

    def __str__(self):
        return f"{self.user} {self.date} {self.status}"


class LeaveRequest(models.Model):
    TYPE_CHOICES = [
        ('CASUAL', 'Casual'),
        ('SICK', 'Sick'),
        ('ANNUAL', 'Annual'),
        ('UNPAID', 'Unpaid'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    start_date = models.DateField()
    end_date = models.DateField()
    leave_type = models.CharField(max_length=8, choices=TYPE_CHOICES, default='CASUAL')
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='PENDING')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='decided_leaves')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class SalaryPayment(models.Model):
    """A month's payslip: basic + allowances − deductions = net."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='salary_payments')
    period = models.CharField(max_length=30)   # e.g. "August 2026"
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    paid_on = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=20, default='CASH')
    note = models.CharField(max_length=255, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='salaries_paid')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-paid_on', '-created_at')

    @property
    def net(self):
        return (self.basic or Decimal('0.00')) + (self.allowances or Decimal('0.00')) - (self.deductions or Decimal('0.00'))

    def __str__(self):
        return f"{self.user} — {self.period} — {self.net}"
