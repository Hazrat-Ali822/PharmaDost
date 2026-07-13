from django.contrib import admin
from .models import Supplier, SupplierPayment


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
	list_display = ('name', 'phone', 'balance')
	search_fields = ('name', 'phone')


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
	list_display = ('supplier', 'amount', 'date', 'method', 'by_user')
	list_filter = ('method', 'date')
	search_fields = ('supplier__name',)
