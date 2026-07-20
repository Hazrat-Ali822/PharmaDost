"""Notify each hospital's pharmacists + admins about medicines at/below reorder level.

Run daily (cron / PythonAnywhere scheduled task):

    python manage.py low_stock_alert
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from inventory.models import Medicine
from accounts.models import Notification
from saas.models import Hospital


class Command(BaseCommand):
    help = "Create in-app notifications for low-stock medicines (run daily)."

    def handle(self, *args, **opts):
        # low_stock() = active medicines with quantity < their reorder_level; with no
        # thread-local set here the manager spans every hospital — grouped below.
        rows = (Medicine.objects.low_stock()
                .values('hospital_id')
                .annotate(n=Count('id')))

        sent = 0
        for row in rows:
            hosp_id, n = row['hospital_id'], row['n']
            if not hosp_id or not n:
                continue
            hospital = Hospital.objects.filter(pk=hosp_id).first()
            if not hospital:
                continue
            msg = f"📉 Low stock: {n} medicine(s) at or below reorder level. Review the reorder report."
            for role in ('PHARMACIST', 'ADMIN'):
                Notification.send_to_role(hospital=hospital, role=role,
                                          message=msg, link='/medicines/reorder/')
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"Sent low-stock alerts for {sent} hospital(s)."))
