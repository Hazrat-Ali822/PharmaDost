"""Give every hospital its own copy of the lab catalogue.

Until now `TestCategory`/`LabTest` had no `hospital` column, so all tenants shared
one price list — and the catalogue editor's bulk save ran over
`LabTest.objects.all()`, meaning one hospital's admin rewrote everybody's prices.

Simply stamping the existing rows with a hospital would be wrong (which one?), and
leaving them all NULL would be worse: `TenantManager` hides `hospital IS NULL`
rows from a hospital-scoped user, so on the next deploy every tenant's lab menu
would come up empty and no test could be ordered.

So: **clone**. Each hospital gets its own copy of the shared catalogue, and that
hospital's existing results are re-pointed at its copy so history keeps resolving
to a row it can still see. The NULL originals stay exactly as they are — they are
the catalogue of the hospital-less desktop/LAN install, where there is no tenant
and `TenantManager` matches them.

With no hospitals in the database (a fresh install, or the desktop build) this is
a no-op.
"""
from django.db import migrations


def clone_per_hospital(apps, schema_editor):
    Hospital = apps.get_model('saas', 'Hospital')
    TestCategory = apps.get_model('lab', 'TestCategory')
    LabTest = apps.get_model('lab', 'LabTest')
    TestResult = apps.get_model('lab', 'TestResult')

    hospitals = list(Hospital.objects.all())
    if not hospitals:
        return

    shared_cats = list(TestCategory.objects.filter(hospital__isnull=True))
    shared_tests = list(LabTest.objects.filter(hospital__isnull=True))
    if not shared_cats and not shared_tests:
        return

    for hospital in hospitals:
        cat_map = {}
        for cat in shared_cats:
            cat_map[cat.pk] = TestCategory.objects.create(name=cat.name,
                                                          hospital=hospital)
        test_map = {}
        for t in shared_tests:
            new_cat = cat_map.get(t.category_id)
            if new_cat is None:            # a category that was already scoped
                continue
            test_map[t.pk] = LabTest.objects.create(
                category=new_cat, name=t.name, price=t.price, unit=t.unit,
                normal_range=t.normal_range, hospital=hospital)

        # Re-point this hospital's history at its own copy, so a printed report
        # still names a test the hospital can look up.
        results = TestResult.objects.filter(
            test_order__patient__hospital=hospital,
            lab_test__hospital__isnull=True).select_related(None)
        for r in results.iterator():
            new_pk = test_map.get(r.lab_test_id)
            if new_pk is not None:
                r.lab_test_id = new_pk.pk
                r.save(update_fields=['lab_test'])


def noop_reverse(apps, schema_editor):
    """Deliberately not reversible in data terms.

    Rolling back would have to decide which hospital's copy is the survivor and
    which prices to throw away. The schema migration reversing on its own drops
    the column, which puts every row back in one shared table — the state this
    migration exists to leave.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0008_labtest_hospital_testcategory_hospital'),
        ('saas', '0001_initial'),
        ('patients', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(clone_per_hospital, noop_reverse),
    ]
