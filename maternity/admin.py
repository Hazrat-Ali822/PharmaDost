from django.contrib import admin

from .models import AntenatalVisit, Birth, Delivery, Pregnancy


@admin.register(Pregnancy)
class PregnancyAdmin(admin.ModelAdmin):
    list_display = ("mother", "lmp", "gravida", "para", "status", "high_risk")
    list_filter = ("status", "high_risk")
    search_fields = ("mother__full_name", "husband_name")


@admin.register(AntenatalVisit)
class AntenatalVisitAdmin(admin.ModelAdmin):
    list_display = ("pregnancy", "date", "bp", "weight")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("mother", "delivered_at", "delivery_type", "outcome")
    list_filter = ("delivery_type", "outcome")


@admin.register(Birth)
class BirthAdmin(admin.ModelAdmin):
    list_display = ("delivery", "sex", "weight_kg", "status", "birth_time")
    list_filter = ("sex", "status")
