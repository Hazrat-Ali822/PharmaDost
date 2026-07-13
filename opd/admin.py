from django.contrib import admin
from .models import Doctor, Appointment, ClinicalRecord, DoctorPayout


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'specialty', 'pmdc_no', 'opd_fee', 'share_percent', 'user')
    search_fields = ('full_name', 'specialty')


@admin.register(DoctorPayout)
class DoctorPayoutAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'date', 'amount', 'payment_method', 'paid_by')
    list_filter = ('payment_method', 'date')
    search_fields = ('doctor__full_name',)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'doctor', 'appointment_date', 'token_no', 'visit_type', 'status')
    list_filter = ('status', 'visit_type', 'appointment_date')


@admin.register(ClinicalRecord)
class ClinicalRecordAdmin(admin.ModelAdmin):
    list_display = ('patient', 'record_type', 'title', 'date', 'doctor')
    list_filter = ('record_type', 'date')
    search_fields = ('patient__full_name', 'title', 'diagnosis')
