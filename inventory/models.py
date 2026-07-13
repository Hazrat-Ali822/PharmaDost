from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from suppliers.models import Supplier
from .query import MedicineQuerySet
from django.conf import settings

from saas.utils import TenantManager, get_current_hospital

class ActiveMedicineManager(models.Manager.from_queryset(MedicineQuerySet)):
    def get_queryset(self):
        qs = super().get_queryset().filter(is_active=True)
        hospital = get_current_hospital()
        if hospital:
            return qs.filter(hospital=hospital)
        return qs

class TenantAllMedicineManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        hospital = get_current_hospital()
        if hospital:
            return qs.filter(hospital=hospital)
        return qs

class StockBatch(models.Model):
    medicine = models.ForeignKey('inventory.Medicine', related_name='batches', on_delete=models.CASCADE)
    batch_number = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    expiry_date = models.DateField()
    received_at = models.DateTimeField(default=timezone.now)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('expiry_date', 'received_at')

    objects = TenantManager()

    def __str__(self):
        return f"{self.medicine.name} batch {self.batch_number or self.id}"

class Medicine(models.Model):
    CATEGORY_CHOICES = (
        ('TABLET', 'Tablet'),
        ('CAPSULE', 'Capsule'),
        ('SYRUP', 'Syrup'),
        ('INJECTION', 'Injection'),
        ('DROPS', 'Drops'),
        ('CREAM', 'Cream / Ointment'),
        ('INHALER', 'Inhaler'),
        ('SACHET', 'Sachet'),
        ('SUPPOSITORY', 'Suppository'),
        ('OTHER', 'Other'),
    )

    name = models.CharField(max_length=255)
    generic_name = models.CharField(max_length=255, blank=True, db_index=True)
    brand = models.CharField(max_length=255, blank=True)
    manufacturer = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True)
    barcode = models.CharField(max_length=64, blank=True, db_index=True)
    image = models.ImageField(upload_to='medicines/', blank=True, null=True)
    pack_size = models.CharField(max_length=50, blank=True)
    units_per_pack = models.PositiveIntegerField(default=1)
    rack_location = models.CharField(max_length=50, blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(0)])
    reorder_level = models.PositiveIntegerField(default=10)

    quantity = models.PositiveIntegerField(default=0)
    expiry_date = models.DateField()
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = ActiveMedicineManager()
    all_objects = TenantAllMedicineManager()

    class Meta:
        unique_together = ('name', 'brand')
        ordering = ('name', 'brand')

    def __str__(self):
        return f"{self.name} ({self.brand})"

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def is_low_stock(self):
        return self.quantity < self.reorder_level

    def soft_delete(self):
        self.is_active = False
        self.save(update_fields=['is_active'])

    def add_stock(self, quantity, batch_number='', expiry_date=None, cost_price=None, supplier=None):
        if quantity < 1:
            raise ValueError('Quantity must be at least 1')
        if expiry_date is None:
            expiry_date = self.expiry_date
        if cost_price is None:
            cost_price = self.price
        batch = StockBatch.objects.create(
            medicine=self,
            batch_number=batch_number,
            quantity=quantity,
            cost_price=cost_price,
            expiry_date=expiry_date,
            supplier=supplier or self.supplier,
            hospital=self.hospital,
        )
        self.quantity += quantity
        self.save(update_fields=['quantity'])
        return batch

    def reduce_stock(self, qty):
        if qty < 0:
            raise ValueError('Quantity must be positive')
        if qty > self.quantity:
            raise ValueError('Not enough stock')
        remaining = qty
        batches = self.batches.filter(quantity__gt=0).order_by('expiry_date', 'received_at')
        consumed = []
        if not batches.exists():
            self.quantity -= qty
            self.save(update_fields=['quantity'])
            return [{'batch_id': None, 'batch_number': '', 'quantity': qty, 'expiry_date': None}]
        for b in batches:
            if remaining <= 0:
                break
            take = b.quantity if b.quantity <= remaining else remaining
            b.quantity -= take
            b.save(update_fields=['quantity'])
            consumed.append({'batch_id': b.id, 'batch_number': b.batch_number, 'quantity': take, 'expiry_date': b.expiry_date})
            remaining -= take
        if remaining > 0:
            raise ValueError('Not enough batch stock available')
        self.quantity -= qty
        self.save(update_fields=['quantity'])
        return consumed

class PurchaseOrder(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    received_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-received_at',)

    objects = TenantManager()

    def __str__(self):
        return f"PO #{self.id} {self.supplier or ''}"

    @property
    def balance(self):
        return self.total - self.paid

class PurchaseItem(models.Model):
    order = models.ForeignKey(PurchaseOrder, related_name='items', on_delete=models.CASCADE)
    medicine = models.ForeignKey('inventory.Medicine', on_delete=models.PROTECT)
    batch_number = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    expiry_date = models.DateField()

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"

class StockAdjustment(models.Model):
    REASON_CHOICES = (
        ('DAMAGE', 'Damage'),
        ('EXPIRY', 'Expiry write-off'),
        ('COUNT', 'Stock count correction'),
        ('OTHER', 'Other'),
    )
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='adjustments')
    qty_change = models.IntegerField(help_text='Negative to remove stock, positive to add')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    notes = models.TextField(blank=True)
    by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    objects = TenantManager()

    def __str__(self):
        return f"Adj {self.qty_change:+d} on {self.batch} ({self.get_reason_display()})"

class PurchaseReturn(models.Model):
    REASON_CHOICES = (
        ('EXPIRY', 'Expired'),
        ('DAMAGE', 'Damaged'),
        ('OTHER', 'Other'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='EXPIRY')
    notes = models.TextField(blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    objects = TenantManager()

    def __str__(self):
        return f"Return #{self.id} {self.supplier or ''}"

class PurchaseReturnItem(models.Model):
    ret = models.ForeignKey(PurchaseReturn, related_name='items', on_delete=models.CASCADE)
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name='return_items')
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    @property
    def line_total(self):
        return self.cost_price * self.quantity

    def __str__(self):
        return f"{self.batch} x {self.quantity}"

class PurchaseRequest(models.Model):
    DRAFT = 'DRAFT'
    RECEIVED = 'RECEIVED'
    CANCELLED = 'CANCELLED'
    STATUS_CHOICES = (
        (DRAFT, 'Draft / Sent'),
        (RECEIVED, 'Received'),
        (CANCELLED, 'Cancelled'),
    )

    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_requests')
    supplier_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_requests')
    created_at = models.DateTimeField(default=timezone.now)
    purchase_order = models.ForeignKey('inventory.PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='from_request')
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    objects = TenantManager()

    def __str__(self):
        return f"Purchase Order #{self.id}"

    @property
    def display_supplier(self):
        return self.supplier.name if self.supplier else (self.supplier_name or '—')

    @property
    def total(self):
        return sum((i.line_total for i in self.items.all()), Decimal('0.00'))

    @property
    def item_count(self):
        return self.items.count()

    @property
    def total_qty(self):
        return sum((i.quantity for i in self.items.all()), 0)

class PurchaseRequestItem(models.Model):
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='items')
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ('id',)

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"

    @property
    def line_total(self):
        return self.cost_price * self.quantity
