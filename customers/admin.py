from django.contrib import admin
from .models import Customer, CustomerPayment


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "shop_name", "type", "phone", "area", "credit_limit", "balance", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("name", "shop_name", "phone")


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "date", "method", "received_by", "linked_sale")
    list_filter = ("method", "date")
    search_fields = ("customer__name", "customer__shop_name")
