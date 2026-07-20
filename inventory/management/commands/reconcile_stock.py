"""Reconcile each Medicine's aggregate `quantity` with the true sum of its batches.

The aggregate `Medicine.quantity` is denormalised for speed but can drift from the
batch ledger over time (legacy imports, partial failures, manual edits). This command
reports and (with --fix) corrects that drift. Safe to run repeatedly; a good cron job.

    python manage.py reconcile_stock          # report only
    python manage.py reconcile_stock --fix     # correct the aggregates
"""
from django.core.management.base import BaseCommand

from inventory.models import Medicine


class Command(BaseCommand):
    help = "Report (and optionally fix) drift between Medicine.quantity and its batch sum."

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true',
                            help='Correct the aggregate quantity to match the batch sum.')

    def handle(self, *args, **opts):
        fix = opts['fix']
        drifted = 0
        # all_objects across every tenant; only medicines that actually have batches
        for med in Medicine.all_objects.all():
            if not med.batches.exists():
                continue
            drift = med.quantity - med.batch_quantity
            if drift == 0:
                continue
            drifted += 1
            tag = med.hospital_id or '-'
            self.stdout.write(
                f"[H{tag}] {med.name} ({med.brand}): aggregate={med.quantity} "
                f"batch_sum={med.batch_quantity} drift={drift:+d}"
            )
            if fix:
                med.reconcile_quantity()

        if drifted == 0:
            self.stdout.write(self.style.SUCCESS("No stock drift found — all aggregates match their batches."))
        elif fix:
            self.stdout.write(self.style.SUCCESS(f"Fixed {drifted} medicine(s)."))
        else:
            self.stdout.write(self.style.WARNING(
                f"{drifted} medicine(s) have drift. Re-run with --fix to correct."))
