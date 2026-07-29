from django.contrib import admin

from .models import ClientAction


@admin.register(ClientAction)
class ClientActionAdmin(admin.ModelAdmin):
    list_display = ("client_uuid", "kind", "status", "hospital", "user", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("client_uuid", "error")
    readonly_fields = [f.name for f in ClientAction._meta.fields]
