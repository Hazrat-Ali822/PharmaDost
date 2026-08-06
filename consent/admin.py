from django.contrib import admin

from .models import ConsentForm, ConsentTemplate


@admin.register(ConsentTemplate)
class ConsentTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "consent_type", "is_active")
    list_filter = ("consent_type", "is_active")
    search_fields = ("title",)


@admin.register(ConsentForm)
class ConsentFormAdmin(admin.ModelAdmin):
    list_display = ("title", "patient", "consent_type", "signed_on")
    list_filter = ("consent_type",)
    search_fields = ("title", "patient__full_name")
