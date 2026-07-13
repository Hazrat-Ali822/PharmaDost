from django.contrib import admin
from .models import (
    Medicine, StockBatch, PurchaseOrder, PurchaseItem,
    StockAdjustment, PurchaseReturn, PurchaseReturnItem,
)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
	list_display = ('name', 'generic_name', 'brand', 'category', 'price', 'wholesale_price', 'quantity', 'reorder_level', 'expiry_date', 'supplier')
	list_filter = ('category', 'supplier', 'expiry_date')
	search_fields = ('name', 'generic_name', 'brand', 'barcode')


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
	list_display = ('medicine', 'batch_number', 'quantity', 'cost_price', 'expiry_date', 'supplier')
	search_fields = ('medicine__name', 'batch_number')
	list_filter = ('expiry_date',)


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
	list_display = ('created_at', 'batch', 'qty_change', 'reason', 'by_user')
	list_filter = ('reason', 'created_at')


class PurchaseReturnItemInline(admin.TabularInline):
	model = PurchaseReturnItem
	extra = 0


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
	list_display = ('id', 'created_at', 'supplier', 'reason', 'total', 'created_by')
	list_filter = ('reason', 'created_at')
	inlines = [PurchaseReturnItemInline]


admin.site.register(PurchaseOrder)
admin.site.register(PurchaseItem)
