from django.contrib import admin
from .models import Sale, SaleItem

class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    autocomplete_fields = ('medicine',)  # 'sale' is implied; we don't need it here

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'sale_type', 'customer', 'customer_name', 'total', 'paid', 'is_returned')
    list_filter = ('sale_type', 'payment_method', 'is_returned')
    date_hierarchy = 'created_at'
    search_fields = ('id', 'customer_name')  # <-- Added so autocomplete to Sale works if referenced
    inlines = [SaleItemInline]

@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ('sale', 'medicine', 'unit_price', 'quantity', 'line_total')
    autocomplete_fields = ('medicine', 'sale')  # requires search_fields on MedicineAdmin and SaleAdmin
