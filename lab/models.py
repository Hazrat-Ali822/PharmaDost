from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from patients.models import Patient


class TestCategory(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class LabTest(models.Model):
    category = models.ForeignKey(TestCategory, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    unit = models.CharField(max_length=50, blank=True)
    normal_range = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.name} ({self.category.name})"


class TestOrder(models.Model):
    STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Sample Collected', 'Sample Collected'),
        ('Result Entered', 'Result Entered'),
        ('Completed', 'Completed'),
        ('Verified', 'Verified'),
        ('Delivered', 'Delivered'),
        # The patient declined, or the doctor withdrew the request. The row stays —
        # "why was this test never done" has to remain answerable — but it leaves
        # the lab's pending queue and its charge comes off the bill.
        ('Cancelled', 'Cancelled'),
    )

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='lab_orders')
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lab_orders'
    )
    tests = models.ManyToManyField('LabTest', through='TestResult')
    order_date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    
    # Cash collection at point of service
    payment_status = models.CharField(max_length=20, default='Pending', choices=[('Pending', 'Pending'), ('Paid', 'Paid')])
    payment_collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='collected_lab_orders')
    payment_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='lab_orders')

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_lab_orders')
    cancel_reason = models.CharField(max_length=255, blank=True)

    @property
    def active_results(self):
        """The tests still to be done — cancelled ones are history, not work."""
        return self.results.filter(is_cancelled=False)

    @property
    def total_price(self):
        """What this order is worth *now*. Reads `results`, not the `tests` M2M,
        because a cancelled test is no longer chargeable — the M2M cannot express
        that and would keep billing for a test the patient refused."""
        return sum(r.lab_test.price for r in self.results.filter(is_cancelled=False)
                   .select_related('lab_test'))

    @property
    def is_cancelled(self):
        return self.status == 'Cancelled'

    def __str__(self):
        return f"Order #{self.id} - {self.patient.full_name}"


class TestResult(models.Model):
    test_order = models.ForeignKey(TestOrder, on_delete=models.CASCADE, related_name='results')
    lab_test = models.ForeignKey(LabTest, on_delete=models.CASCADE)
    result_value = models.CharField(max_length=100, blank=True)
    normal_range = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    remarks = models.TextField(blank=True)

    # Cancelling one test off a multi-test order. Soft, with a reason and a name
    # against it: the printed report has to be able to say "Cancelled — patient
    # refused" rather than silently showing two tests where the doctor asked three.
    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_lab_tests')
    cancel_reason = models.CharField(max_length=255, blank=True)

    def save(self, *args, **kwargs):
        if not self.normal_range and self.lab_test:
            self.normal_range = self.lab_test.normal_range
        if not self.unit and self.lab_test:
            self.unit = self.lab_test.unit
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lab_test.name} result for {self.test_order.patient.full_name}"
