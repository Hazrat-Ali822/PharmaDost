from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from saas.utils import TenantManager


class Customer(models.Model):
    RETAIL = "RETAIL"
    WHOLESALE = "WHOLESALE"
    TYPE_CHOICES = (
        (RETAIL, "Retail Khata"),
        (WHOLESALE, "Wholesale"),
    )

    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=RETAIL)
    name = models.CharField(max_length=255)
    shop_name = models.CharField(max_length=255, blank=True)   # wholesale
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    area = models.CharField(max_length=100, blank=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))  # outstanding owed by customer
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ("name",)

    def __str__(self):
        label = self.shop_name or self.name
        return f"{label} ({self.get_type_display()})"

    @property
    def available_credit(self):
        return self.credit_limit - self.balance


class CustomerPayment(models.Model):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("CARD", "Card"),
        ("BANK", "Bank Transfer"),
        ("OTHER", "Other"),
    )
    customer = models.ForeignKey(Customer, related_name="payments", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="CASH")
    notes = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    linked_sale = models.ForeignKey(
        "sales.Sale", null=True, blank=True, on_delete=models.SET_NULL, related_name="customer_payments"
    )
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = TenantManager()

    class Meta:
        ordering = ("-date", "-created_at")

    def __str__(self):
        return f"Payment {self.amount} from {self.customer}"
