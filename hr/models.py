"""Staff HR — the people side of the hospital: who works here, their attendance,
leave, and monthly salary. Kept separate from `accounts.User` (which is only auth
+ role) so payroll data doesn't bloat the login model, and so a StaffProfile row is
optional — a user without one simply has no salary set yet.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from saas.utils import TenantManager


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
