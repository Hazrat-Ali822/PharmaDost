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

    def __str__(self):
        return f"Order #{self.id} - {self.patient.full_name}"


class TestResult(models.Model):
    test_order = models.ForeignKey(TestOrder, on_delete=models.CASCADE, related_name='results')
    lab_test = models.ForeignKey(LabTest, on_delete=models.CASCADE)
    result_value = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)

    def __str__(self):
        return f"{self.lab_test.name} result for {self.test_order.patient.full_name}"
