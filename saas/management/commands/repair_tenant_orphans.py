from django.core.management.base import BaseCommand
from saas.models import Hospital
from accounts.models import User
from patients.models import Patient
from inventory.models import Medicine
from billing.models import Invoice
from sales.models import Sale

class Command(BaseCommand):
    help = "Repair tenant orphans by assigning NULL hospital fields to the correct hospital."

    def handle(self, *args, **options):
        hospitals = list(Hospital.objects.all())
        if not hospitals:
            self.stdout.write("No hospitals found in the database. Add a hospital first.")
            return

        # If there's only one hospital, use it as default
        default_hospital = hospitals[0]
        self.stdout.write(f"Default hospital chosen for orphans: {default_hospital.name} (ID: {default_hospital.id})")

        # 1. Update Users
        users_updated = User.objects.filter(hospital__isnull=True, is_superuser=False).update(hospital=default_hospital)
        self.stdout.write(f"Updated {users_updated} user(s) to hospital {default_hospital.name}")

        # 2. Update Patients
        patients_updated = Patient.objects.filter(hospital__isnull=True).update(hospital=default_hospital)
        self.stdout.write(f"Updated {patients_updated} patient(s) to hospital {default_hospital.name}")

        # 3. Update Medicines
        meds_updated = Medicine.all_objects.filter(hospital__isnull=True).update(hospital=default_hospital)
        self.stdout.write(f"Updated {meds_updated} medicine(s) to hospital {default_hospital.name}")

        # 4. Update Invoices
        invoices_updated = 0
        for inv in Invoice.objects.filter(hospital__isnull=True):
            if inv.patient and inv.patient.hospital:
                inv.hospital = inv.patient.hospital
                inv.save()
                invoices_updated += 1
            else:
                inv.hospital = default_hospital
                inv.save()
                invoices_updated += 1
        self.stdout.write(f"Updated {invoices_updated} invoice(s) to hospital {default_hospital.name}")

        self.stdout.write("Database tenant repair completed successfully!")
