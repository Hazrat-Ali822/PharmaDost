from datetime import timedelta
from django.utils import timezone
from django.db import models
from django.db.models import F


class MedicineQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def low_stock(self, threshold=None):
        """Low stock using each medicine's own reorder_level, or a fixed threshold if given."""
        qs = self.active()
        if threshold is None:
            return qs.filter(quantity__lt=F('reorder_level'))
        return qs.filter(quantity__lt=threshold)

    def expiring_soon(self, days=30):
        today = timezone.localdate()
        end = today + timedelta(days=days)
        return self.active().filter(expiry_date__range=(today, end))