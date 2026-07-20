from django.db import migrations


def assign_orphans_to_sole_hospital(apps, schema_editor):
    """If exactly one Hospital exists, stamp every hospital-less Supplier /
    SupplierPayment with it so pre-existing single-tenant data stays visible
    once the TenantManager starts filtering. No-op for fresh or multi-tenant DBs."""
    Hospital = apps.get_model('saas', 'Hospital')
    Supplier = apps.get_model('suppliers', 'Supplier')
    SupplierPayment = apps.get_model('suppliers', 'SupplierPayment')

    if Hospital.objects.count() != 1:
        return
    hospital = Hospital.objects.first()
    Supplier.objects.filter(hospital__isnull=True).update(hospital=hospital)
    SupplierPayment.objects.filter(hospital__isnull=True).update(hospital=hospital)


class Migration(migrations.Migration):

    dependencies = [
        ('suppliers', '0003_supplier_hospital_supplierpayment_hospital_and_more'),
        ('saas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(assign_orphans_to_sole_hospital, migrations.RunPython.noop),
    ]
