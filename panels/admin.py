from django.contrib import admin

from .models import Panel, PanelPayment


@admin.register(Panel)
class PanelAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "phone", "is_active", "created_at")
    list_filter = ("type", "is_active")
    search_fields = ("name", "contact_person", "phone")


@admin.register(PanelPayment)
class PanelPaymentAdmin(admin.ModelAdmin):
    list_display = ("panel", "amount", "date", "method", "reference")
    list_filter = ("method", "date")
    search_fields = ("panel__name", "reference")
