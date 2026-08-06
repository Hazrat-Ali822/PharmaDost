from django.contrib import admin

from .models import Vaccine, VaccinationRecord


@admin.register(Vaccine)
class VaccineAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "recommended_age", "doses_in_series", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(VaccinationRecord)
class VaccinationRecordAdmin(admin.ModelAdmin):
    list_display = ("patient", "vaccine", "dose_number", "date_given", "next_due_date")
    search_fields = ("patient__full_name", "vaccine__code")
