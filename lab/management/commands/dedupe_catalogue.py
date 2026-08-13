"""Merge duplicate lab tests (and scan types) within a hospital's catalogue.

Reported from the live demo: the ordering screen offered "CBC (Complete Blood
Count)" four times, and the copies were not identical — one had a unit and a
reference range, the others were blank, so which one the doctor happened to tick
decided whether the printed report carried a normal range at all.

Nothing enforced uniqueness: `seed_lab`, `import_labs_scans`, `seed_public_demo`
and hand-entry all reached the same table, each `get_or_create`-ing on a slightly
different key. Once the catalogue became per-hospital those duplicates were
cloned along with everything else.

The survivor is the **best-populated** row, not simply the oldest: merging into a
blank row would throw away the unit and reference range that make the report
useful. Existing results are re-pointed at the survivor so no history is lost, and
only then are the losers deleted.

    python manage.py dedupe_catalogue --dry-run     # show what would merge
    python manage.py dedupe_catalogue               # do it
    python manage.py dedupe_catalogue --hospital shaheen-health-care
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from imaging.models import ScanType
from lab.models import LabTest, TestCategory, TestResult


def _score(test):
    """How complete is this row? Higher wins the merge."""
    return (bool((test.unit or '').strip())
            + bool((test.normal_range or '').strip())
            + bool(test.price and test.price > 0))


class Command(BaseCommand):
    help = "Merge duplicate lab tests / scan types inside each hospital's catalogue."

    def add_arguments(self, parser):
        parser.add_argument('--hospital', dest='slug', default=None,
                            help='Only this hospital (slug). Default: all of them.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be merged and change nothing.')

    @transaction.atomic
    def handle(self, *args, **options):
        from saas.models import Hospital

        if options['slug']:
            hospitals = list(Hospital.objects.filter(slug=options['slug']))
            if not hospitals:
                self.stderr.write(f"No hospital with slug '{options['slug']}'.")
                return
        else:
            hospitals = list(Hospital.objects.all()) + [None]

        dry = options['dry_run']
        merged_tests = merged_scans = merged_cats = 0

        for hospital in hospitals:
            label = hospital.name if hospital else 'hospital-less install'

            # --- lab tests: key on (category name, test name), case-insensitive
            groups = {}
            for t in LabTest.all_objects.filter(hospital=hospital).select_related('category'):
                key = ((t.category.name or '').strip().lower(),
                       (t.name or '').strip().lower())
                groups.setdefault(key, []).append(t)
            for (cat_name, name), rows in groups.items():
                if len(rows) < 2:
                    continue
                rows.sort(key=lambda r: (-_score(r), r.pk))
                keep, losers = rows[0], rows[1:]
                self.stdout.write(
                    f"[{label}] {name}: {len(rows)} copies -> keeping #{keep.pk} "
                    f"(unit={keep.unit!r} range={keep.normal_range!r} "
                    f"price={keep.price}); dropping "
                    f"{', '.join('#' + str(r.pk) for r in losers)}")
                merged_tests += len(losers)
                if dry:
                    continue
                loser_ids = [r.pk for r in losers]
                # Re-point history FIRST — deleting a LabTest cascades to its
                # TestResults, which would silently erase entered results.
                TestResult.objects.filter(lab_test_id__in=loser_ids).update(lab_test=keep)
                LabTest.all_objects.filter(pk__in=loser_ids).delete()

            # --- scan types: key on (modality, name)
            sgroups = {}
            for s in ScanType.all_objects.filter(hospital=hospital):
                sgroups.setdefault((s.modality, (s.name or '').strip().lower()), []).append(s)
            for key, rows in sgroups.items():
                if len(rows) < 2:
                    continue
                rows.sort(key=lambda r: (-(r.price or 0), r.pk))
                keep, losers = rows[0], rows[1:]
                self.stdout.write(f"[{label}] scan {keep.name}: {len(rows)} copies "
                                  f"-> keeping #{keep.pk}")
                merged_scans += len(losers)
                if not dry:
                    # ImagingStudy holds no FK to ScanType (it copies the name and
                    # price onto itself), so there is nothing to re-point.
                    ScanType.all_objects.filter(pk__in=[r.pk for r in losers]).delete()

            # --- empty duplicate categories left behind
            cgroups = {}
            for c in TestCategory.all_objects.filter(hospital=hospital):
                cgroups.setdefault((c.name or '').strip().lower(), []).append(c)
            for name, rows in cgroups.items():
                if len(rows) < 2:
                    continue
                rows.sort(key=lambda r: r.pk)
                keep, losers = rows[0], rows[1:]
                if not dry:
                    for loser in losers:
                        LabTest.all_objects.filter(category=loser).update(category=keep)
                    TestCategory.all_objects.filter(
                        pk__in=[r.pk for r in losers]).delete()
                merged_cats += len(losers)

        verb = 'would merge' if dry else 'merged'
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {merged_tests} lab test(s), {merged_scans} scan(s), "
            f"{merged_cats} category(ies)."))
        if dry:
            self.stdout.write('Dry run — nothing was changed.')
