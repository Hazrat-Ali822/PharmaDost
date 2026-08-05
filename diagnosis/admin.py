from django.contrib import admin

from .models import DiagnosisCode, PatientDiagnosis


@admin.register(DiagnosisCode)
class DiagnosisCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "category", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("code", "title")


@admin.register(PatientDiagnosis)
class PatientDiagnosisAdmin(admin.ModelAdmin):
    list_display = ("patient", "code", "diagnosed_on", "doctor")
    search_fields = ("patient__full_name", "code__code", "code__title")
