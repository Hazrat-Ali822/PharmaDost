from django.core.management.base import BaseCommand
from django.db import transaction
from saas.models import Hospital
from saas.catalog_seeder import seed_hospital_catalogs


class Command(BaseCommand):
    help = "Seeds comprehensive Lab Tests and Radiology/Ultrasound/X-Ray catalogs for all hospitals. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument(
            '--hospital', dest='slug', default=None,
            help="Seed only this specific hospital (by slug). Default: all hospitals."
        )

    def handle(self, *args, **options):
        slug = options.get('slug')
        if slug:
            hospitals = list(Hospital.objects.filter(slug=slug))
            if not hospitals:
                self.stderr.write(self.style.ERROR(f"No hospital found with slug '{slug}'."))
                return
        else:
            hospitals = list(Hospital.objects.all()) or [None]

        total_cats = 0
        total_labs = 0
        total_scans = 0

        for hosp in hospitals:
            name = hosp.name if hosp else "Hospital-less (Desktop / LAN)"
            stats = seed_hospital_catalogs(hosp)
            total_cats += stats["categories_created"]
            total_labs += stats["lab_tests_created"]
            total_scans += stats["scans_created"]
            self.stdout.write(f"✓ {name}: +{stats['categories_created']} categories, +{stats['lab_tests_created']} lab tests, +{stats['scans_created']} scans.")

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded diagnostic catalogs! Total new categories: {total_cats}, lab tests: {total_labs}, scans: {total_scans}."
        ))
