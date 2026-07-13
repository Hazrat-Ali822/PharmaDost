from django.contrib import admin

from .models import ImagingStudy


@admin.register(ImagingStudy)
class ImagingStudyAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "modality", "study_name", "status", "study_date", "performed_by")
    list_filter = ("modality", "status")
    search_fields = ("patient__full_name", "study_name")
    date_hierarchy = "study_date"
