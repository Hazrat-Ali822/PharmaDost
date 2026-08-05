from django.contrib import admin

from .models import EmergencyCase


@admin.register(EmergencyCase)
class EmergencyCaseAdmin(admin.ModelAdmin):
    list_display = ("pk", "patient", "triage", "disposition", "is_mlc", "arrival_time")
    list_filter = ("triage", "disposition", "is_mlc", "mode_of_arrival")
    search_fields = ("patient__full_name", "chief_complaint", "mlc_no")
