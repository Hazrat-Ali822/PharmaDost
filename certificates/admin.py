from django.contrib import admin

from .models import BirthCertificate, DeathCertificate


@admin.register(BirthCertificate)
class BirthCertificateAdmin(admin.ModelAdmin):
    list_display = ("serial_no", "child_name", "mother_name", "date_of_birth", "registered_on")
    search_fields = ("serial_no", "child_name", "mother_name", "father_name")


@admin.register(DeathCertificate)
class DeathCertificateAdmin(admin.ModelAdmin):
    list_display = ("serial_no", "deceased_name", "date_of_death", "cause_of_death", "is_mlc")
    list_filter = ("is_mlc",)
    search_fields = ("serial_no", "deceased_name", "cnic")
