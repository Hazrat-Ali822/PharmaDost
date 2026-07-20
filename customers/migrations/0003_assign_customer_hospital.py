from django.db import migrations


def assign_orphans_to_sole_hospital(apps, schema_editor):
    """If exactly one Hospital exists, stamp every hospital-less Customer /
    CustomerPayment with it so pre-existing single-tenant data stays visible
    once the TenantManager starts filtering. No-op for fresh or multi-tenant DBs."""
    Hospital = apps.get_model('saas', 'Hospital')
    Customer = apps.get_model('customers', 'Customer')
    CustomerPayment = apps.get_model('customers', 'CustomerPayment')

    if Hospital.objects.count() != 1:
        return
    hospital = Hospital.objects.first()
    Customer.objects.filter(hospital__isnull=True).update(hospital=hospital)
    CustomerPayment.objects.filter(hospital__isnull=True).update(hospital=hospital)


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_customer_hospital_customerpayment_hospital'),
        ('saas', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(assign_orphans_to_sole_hospital, migrations.RunPython.noop),
    ]
