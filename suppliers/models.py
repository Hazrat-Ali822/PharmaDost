from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from saas.utils import TenantManager


class Supplier(models.Model):
	name = models.CharField(max_length=255)
	phone = models.CharField(max_length=30)
	address = models.TextField(blank=True)
	balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))  # payable we owe
	hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

	objects = TenantManager()

	class Meta:
		ordering = ('name',)
		constraints = [
			models.UniqueConstraint(fields=['name', 'hospital'], name='uniq_supplier_name_per_hospital'),
		]

	def __str__(self):
		return self.name


class SupplierPayment(models.Model):
	METHOD_CHOICES = (
		('CASH', 'Cash'),
		('BANK', 'Bank Transfer'),
		('CHEQUE', 'Cheque'),
		('OTHER', 'Other'),
	)
	supplier = models.ForeignKey(Supplier, related_name='payments', on_delete=models.PROTECT)
	amount = models.DecimalField(max_digits=12, decimal_places=2)
	date = models.DateField(default=timezone.localdate)
	method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='CASH')
	notes = models.CharField(max_length=255, blank=True)
	by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
	hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)
	created_at = models.DateTimeField(default=timezone.now)

	objects = TenantManager()

	class Meta:
		ordering = ('-date', '-created_at')

	def __str__(self):
		return f"Payment {self.amount} to {self.supplier}"
