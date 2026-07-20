from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from inventory.models import Medicine, StockBatch
from saas.utils import TenantManager


class Sale(models.Model):
    RETAIL = "RETAIL"
    WHOLESALE = "WHOLESALE"
    SALE_TYPE_CHOICES = (
        (RETAIL, "Retail"),
        (WHOLESALE, "Wholesale"),
    )
    PAYMENT_CHOICES = (
        ("CASH", "Cash"),
        ("CARD", "Card"),
        ("CREDIT", "Credit / Khata"),
    )

    sale_type = models.CharField(max_length=10, choices=SALE_TYPE_CHOICES, default=RETAIL)
    customer = models.ForeignKey(
        "customers.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    patient = models.ForeignKey(
        "patients.Patient", null=True, blank=True, on_delete=models.SET_NULL, related_name="pharmacy_sales"
    )
    customer_name = models.CharField(max_length=255, blank=True)  # walk-in

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))  # STORED
    paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default="CASH")

    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="sales"
    )
    is_returned = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    objects = TenantManager()

    def __str__(self):
        return f"Sale #{self.id} - {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def balance(self):
        return self.total - self.paid

    @property
    def computed_total(self):
        """Recompute from line items (used for validation / display fallback)."""
        return sum((item.line_total for item in self.items.all()), Decimal("0.00")) - self.discount


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    batch = models.ForeignKey(StockBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="sale_items")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    # actual cost of the dispensed batch, captured at sale time for accurate margins
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity - self.discount

    @property
    def line_cost(self):
        return self.cost_price * self.quantity

    @property
    def line_profit(self):
        return self.line_total - self.line_cost


class WholesaleOrder(models.Model):
    """A large wholesale order sheet / quotation. It holds many line items but does
    NOT touch stock until it is converted into an actual Sale (bill). Built to enter
    100-1000 items fast (paste / quick-add / repeat a previous order)."""
    DRAFT = "DRAFT"
    BILLED = "BILLED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = (
        (DRAFT, "Draft / Quotation"),
        (BILLED, "Billed"),
        (CANCELLED, "Cancelled"),
    )

    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="wholesale_orders")
    customer_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="wholesale_orders")
    created_at = models.DateTimeField(default=timezone.now)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="from_order")
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    objects = TenantManager()

    def __str__(self):
        return f"Wholesale Order #{self.id}"

    @property
    def display_customer(self):
        if self.customer:
            return self.customer.shop_name or self.customer.name
        return self.customer_name or "—"

    @property
    def total(self):
        return sum((i.line_total for i in self.items.all()), Decimal("0.00"))

    @property
    def item_count(self):
        return self.items.count()

    @property
    def total_qty(self):
        return sum((i.quantity for i in self.items.all()), 0)


class WholesaleOrderItem(models.Model):
    order = models.ForeignKey(WholesaleOrder, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity
