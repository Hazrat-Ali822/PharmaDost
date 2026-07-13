from django.contrib import admin
from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('brand_name', 'brand_tagline', 'primary_color', 'accent_color', 'updated_at')
