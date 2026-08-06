from django.contrib import admin

from .models import BloodDonor, BloodIssue, BloodUnit


@admin.register(BloodDonor)
class BloodDonorAdmin(admin.ModelAdmin):
    list_display = ("full_name", "blood_group", "phone", "last_donation_date")
    list_filter = ("blood_group",)
    search_fields = ("full_name", "cnic", "phone")


@admin.register(BloodUnit)
class BloodUnitAdmin(admin.ModelAdmin):
    list_display = ("bag_number", "blood_group", "component", "status", "expiry_date")
    list_filter = ("blood_group", "component", "status")
    search_fields = ("bag_number",)


@admin.register(BloodIssue)
class BloodIssueAdmin(admin.ModelAdmin):
    list_display = ("unit", "patient", "issued_on")
    search_fields = ("unit__bag_number", "patient__full_name")
