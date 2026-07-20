"""Notify each hospital's pharmacists + admins about expired / near-expiry stock.

Meant to be run on a daily schedule (cron / PythonAnywhere scheduled task):

    python manage.py expiry_alert            # 30-day horizon
    python manage.py expiry_alert --days 60
"""
from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import StockBatch
from accounts.models import Notification
from saas.models import Hospital


class Command(BaseCommand):
    help = "Create in-app notifications for expired / near-expiry stock (run daily)."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Near-expiry horizon in days (default 30).')

    def handle(self, *args, **opts):
        days = max(1, opts['days'])
        today = timezone.localdate()
        horizon = today + timedelta(days=days)

        # StockBatch.objects is tenant-scoped, but with no thread-local set here it
        # returns every hospital's batches — exactly what we want for a global job.
        batches = StockBatch.objects.filter(quantity__gt=0, expiry_date__lte=horizon)

        counts = defaultdict(lambda: [0, 0])  # hospital_id -> [expired, soon]
        for b in batches.only('expiry_date', 'hospital_id'):
            counts[b.hospital_id][0 if b.expiry_date < today else 1] += 1

        sent = 0
        for hosp_id, (expired, soon) in counts.items():
            if not hosp_id:
                continue  # hospital-less/demo rows can't be notified per-tenant
            hospital = Hospital.objects.filter(pk=hosp_id).first()
            if not hospital:
                continue
            msg = (f"⏰ Stock expiry: {expired} batch(es) expired, {soon} expiring "
                   f"within {days} days. Review & return to supplier.")
            for role in ('PHARMACIST', 'ADMIN'):
                Notification.send_to_role(hospital=hospital, role=role,
                                          message=msg, link='/medicines/expiry/')
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"Sent expiry alerts for {sent} hospital(s)."))
