from django.contrib import admin

from .models import Invoice, InvoiceItem, Expense, CashClosing, PatientPayment


@admin.register(PatientPayment)
class PatientPaymentAdmin(admin.ModelAdmin):
    list_display = ('patient', 'amount', 'payment_method', 'collected_by', 'created_at')
    list_filter = ('payment_method', 'created_at')
    search_fields = ('patient__full_name',)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'patient', 'total', 'paid', 'payment_method', 'created_at')
    list_filter = ('payment_method', 'created_at')
    search_fields = ('patient__full_name',)
    inlines = [InvoiceItemInline]


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('id', 'date', 'category', 'description', 'amount', 'payment_method', 'recorded_by')
    list_filter = ('category', 'date')
    search_fields = ('description',)
    date_hierarchy = 'date'


@admin.register(CashClosing)
class CashClosingAdmin(admin.ModelAdmin):
    list_display = ('date', 'opening', 'cash_in', 'cash_out', 'expected', 'counted', 'difference', 'closed_by')
    date_hierarchy = 'date'
