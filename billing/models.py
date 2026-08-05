from decimal import Decimal
from django.db import models
from django.utils import timezone
from accounts.models import User
from patients.models import Patient
from opd.models import Appointment
from saas.utils import TenantManager


class ActiveInvoiceManager(TenantManager):
    def get_queryset(self):
        return super().get_queryset().filter(status='ACTIVE')


class Invoice(models.Model):
    # Human-facing accounting number (INV-2026-00001), allocated per hospital from
    # the locked SiteSettings counter in save(). Null on rows created before this
    # existed — display_no falls back to "#id" for those.
    number = models.CharField(max_length=32, null=True, blank=True, default=None)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='invoices')
    appointment = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    payment_method = models.CharField(max_length=20, default='CASH')
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_invoices')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, default='ACTIVE', choices=[('ACTIVE', 'Active'), ('VOID', 'Void / Cancelled')])

    # Panel / Insurance / Sehat Card billing. When `panel` is set the bill is a
    # claim against that payer: the amount it owes is `balance` (total minus any
    # co-pay collected from the patient via `paid`). PROTECT so a panel with claims
    # cannot be deleted out from under its history.
    CLAIM_STATUS_CHOICES = [
        ('PENDING', 'Pending Submission'),
        ('SUBMITTED', 'Submitted'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid by Panel'),
        ('REJECTED', 'Rejected'),
    ]
    panel = models.ForeignKey('panels.Panel', on_delete=models.PROTECT, null=True, blank=True, related_name='invoices')
    claim_status = models.CharField(max_length=12, choices=CLAIM_STATUS_CHOICES, blank=True, default='')
    claim_number = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                fields=['hospital', 'number'],
                condition=models.Q(number__isnull=False),
                name='uniq_invoice_number_per_hospital'),
        ]

    objects = ActiveInvoiceManager()
    all_objects = TenantManager()

    def save(self, *args, **kwargs):
        # Allocate the accounting number on first save, before the row is written.
        # The tenant is resolved here (not via the pre_save signal, which fires
        # inside super().save() — too late) so seeds, imports and offline replay all
        # get a numbered invoice against the right hospital's counter.
        if self._state.adding and not self.number:
            from saas.utils import get_current_hospital
            from .services import next_invoice_number
            hospital = self.hospital or get_current_hospital()
            self.number = next_invoice_number(hospital)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Invoice {self.display_no}'

    @property
    def display_no(self):
        """The accounting number if allocated, else the legacy '#id'."""
        return self.number or f'#{self.pk}'

    @property
    def balance(self):
        return (self.total or Decimal('0.00')) - (self.paid or Decimal('0.00'))

    @property
    def is_paid(self):
        return self.balance <= 0


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='items', on_delete=models.CASCADE)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return self.description


class Expense(models.Model):
    """Money going OUT of the business — rent, salaries, utilities, supplies…
    Used by the Day Book to compute net cash (income − expenses)."""

    CATEGORY_CHOICES = (
        ('RENT', 'Rent'),
        ('SALARY', 'Salaries / Wages'),
        ('UTILITIES', 'Utilities (electricity, water, gas)'),
        ('SUPPLIES', 'Supplies / Consumables'),
        ('MAINTENANCE', 'Repairs & Maintenance'),
        ('MARKETING', 'Marketing'),
        ('TAX', 'Tax / Govt fees'),
        ('OTHER', 'Other'),
    )

    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='OTHER')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_method = models.CharField(max_length=20, default='CASH')
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='expenses')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-date', '-created_at')

    objects = TenantManager()

    def __str__(self):
        return f'{self.get_category_display()} — {self.amount}'


class PatientPayment(models.Model):
    """A payment a patient makes against their overall balance. The collected
    amount is allocated across their outstanding invoices & pharmacy sales
    (oldest first). Gives an explicit "who paid how much, when" record."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_method = models.CharField(max_length=20, default='CASH')
    note = models.CharField(max_length=255, blank=True)
    collected_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='collected_payments')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    objects = TenantManager()

    def __str__(self):
        return f'{self.patient} paid {self.amount}'


class CashClosing(models.Model):
    """A day-end cash reconciliation: expected cash (opening + cash in − cash out)
    vs the cash actually counted in the drawer. One per date (locks the day)."""

    date = models.DateField()
    opening = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    cash_in = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    cash_out = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    expected = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    counted = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    note = models.CharField(max_length=255, blank=True)
    closed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='cash_closings')
    closed_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-date',)
        unique_together = ('date', 'hospital')

    objects = TenantManager()

    def __str__(self):
        return f'Cash closing {self.date}'
