"""Seed the Pakistan EPI vaccine schedule (idempotent).

    python manage.py seed_epi

Vaccine is a global catalogue, so this seeds once for the whole install.
"""
from django.core.management.base import BaseCommand

from vaccination.models import Vaccine

# (code, name, recommended age, doses in series)
EPI = [
    ('BCG', 'BCG', 'At birth', 1),
    ('OPV0', 'OPV-0 (birth dose)', 'At birth', 1),
    ('HEPB0', 'Hepatitis B (birth dose)', 'At birth', 1),
    ('OPV1', 'OPV-1', '6 weeks', 3),
    ('PENTA1', 'Pentavalent-1 (DTP-HepB-Hib)', '6 weeks', 3),
    ('PCV1', 'PCV-1 (Pneumococcal)', '6 weeks', 3),
    ('ROTA1', 'Rota-1', '6 weeks', 2),
    ('OPV2', 'OPV-2', '10 weeks', 3),
    ('PENTA2', 'Pentavalent-2', '10 weeks', 3),
    ('PCV2', 'PCV-2', '10 weeks', 3),
    ('ROTA2', 'Rota-2', '10 weeks', 2),
    ('OPV3', 'OPV-3', '14 weeks', 3),
    ('PENTA3', 'Pentavalent-3', '14 weeks', 3),
    ('PCV3', 'PCV-3', '14 weeks', 3),
    ('IPV', 'IPV (Injectable Polio)', '14 weeks', 1),
    ('MEASLES1', 'Measles-1', '9 months', 2),
    ('MEASLES2', 'Measles-2', '15 months', 2),
    ('TT', 'Tetanus Toxoid (mother)', 'Pregnancy', 5),
]


class Command(BaseCommand):
    help = "Seed the Pakistan EPI vaccine schedule (idempotent)."

    def handle(self, *args, **options):
        created = 0
        for i, (code, name, age, doses) in enumerate(EPI):
            _, made = Vaccine.objects.get_or_create(
                code=code,
                defaults={'name': name, 'recommended_age': age,
                          'doses_in_series': doses, 'sequence': i},
            )
            created += 1 if made else 0
        self.stdout.write(self.style.SUCCESS(
            f"EPI schedule ready — {created} new, {Vaccine.objects.count()} total."))
