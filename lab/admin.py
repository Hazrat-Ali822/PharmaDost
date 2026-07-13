from django.contrib import admin
from .models import TestCategory, LabTest, TestOrder, TestResult
from patients.models import Patient


class TestResultInline(admin.TabularInline):
	model = TestResult
	extra = 0


@admin.register(TestOrder)
class TestOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "order_date", "status", "ordered_by")
    list_filter = ("status", "order_date")
    search_fields = ("patient__full_name", "id")
    inlines = [TestResultInline]


@admin.register(TestCategory)
class TestCategoryAdmin(admin.ModelAdmin):
	search_fields = ("name",)


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
	list_display = ("name", "category", "price", "unit")
	list_filter = ("category",)
	search_fields = ("name", "category__name")


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("mrn", "full_name", "phone", "gender")
    search_fields = ("full_name", "phone", "mrn")


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("test_order", "lab_test", "result_value")
    search_fields = ("test_order__patient__full_name", "lab_test__name")