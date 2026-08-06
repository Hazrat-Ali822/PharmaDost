from django.contrib import admin

from .models import Referral


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("patient", "direction", "facility", "urgency", "status", "referral_date")
    list_filter = ("direction", "urgency", "status")
    search_fields = ("patient__full_name", "facility", "reason")
