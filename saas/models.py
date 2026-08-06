from django.db import models

class Hospital(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    expiry_date = models.DateField()
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    enabled_modules = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class HospitalPayment(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField()
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.hospital.name} - Rs {self.amount} on {self.payment_date}"

class PlatformExpense(models.Model):
    title = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expense_date = models.DateField()
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - Rs {self.amount}"


def _backup_upload_to(instance, filename):
    from django.utils.text import slugify
    slug = slugify(instance.install_name) or "install"
    return f"desktop_backups/{slug}/{filename}"


class DesktopBackup(models.Model):
    """A full data snapshot (SQLite DB + media, zipped) uploaded by an offline
    desktop/LAN install when it has internet. This is **cloud backup, not sync** —
    the file is stored as-is for the SaaS owner to hold and hand back if the clinic's
    computer is lost, stolen or fails; it is not merged into the hosted database.

    Not tenant-scoped: these come from external LAN installs (identified by their
    signed licence), not hosted tenants, so only the SaaS owner (superuser) sees
    them. The owner keeps the last few per install (older ones are rotated off).
    """
    install_name = models.CharField(max_length=255, db_index=True)
    file = models.FileField(upload_to=_backup_upload_to)
    size_bytes = models.BigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.install_name} — {self.uploaded_at:%Y-%m-%d %H:%M}"

    @property
    def size_mb(self):
        return round((self.size_bytes or 0) / (1024 * 1024), 1)
