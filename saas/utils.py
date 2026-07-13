import threading
from django.db import models

_thread_locals = threading.local()

def get_current_hospital():
    return getattr(_thread_locals, 'hospital', None)

def set_current_hospital(hospital):
    _thread_locals.hospital = hospital

def clear_current_hospital():
    if hasattr(_thread_locals, 'hospital'):
        del _thread_locals.hospital

class TenantManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        hospital = get_current_hospital()
        if hospital:
            return qs.filter(hospital=hospital)
        return qs
