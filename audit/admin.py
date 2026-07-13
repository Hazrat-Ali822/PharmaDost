from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'model_name', 'object_repr')
    list_filter = ('action', 'model_name', 'timestamp')
    search_fields = ('object_repr', 'description')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False  # audit rows are written by the system only
