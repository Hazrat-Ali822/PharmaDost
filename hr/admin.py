from django.contrib import admin

from .models import Attendance, LeaveRequest, SalaryPayment, StaffProfile


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "designation", "monthly_salary", "joining_date")
    search_fields = ("user__email", "designation")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "status")
    list_filter = ("status", "date")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "start_date", "end_date", "leave_type", "status")
    list_filter = ("status", "leave_type")


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "period", "basic", "allowances", "deductions", "paid_on")
    list_filter = ("paid_on",)
