"""seed_public_demo must build the demo tenant with data in every module,
scoped to its own hospital (never leaking into a real/hospital-less tenant)."""
from django.core.management import call_command
from django.test import TestCase

from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital


class SeedPublicDemoTest(TestCase):
    def tearDown(self):
        clear_current_hospital()

    def test_it_seeds_every_module_scoped_to_the_demo_hospital(self):
        call_command('seed_public_demo')

        demo = Hospital.objects.get(slug='demo')
        set_current_hospital(demo)

        from accounts.models import User
        from patients.models import Patient
        from inventory.models import Medicine
        from sales.models import Sale
        from opd.models import Doctor, Appointment, Department
        from prescriptions.models import Prescription
        from lab.models import TestOrder
        from imaging.models import ImagingStudy
        from ipd.models import Admission, VitalsObservation, ShiftHandover
        from ot.models import SurgeryRecord
        from billing.models import Invoice, Expense

        # the demo login exists, is scoped to the demo hospital, and is NOT a superuser
        demo_admin = User.objects.get(email='demo@sehatyar.online')
        self.assertEqual(demo_admin.hospital_id, demo.id)
        self.assertFalse(demo_admin.is_superuser)
        self.assertTrue(demo_admin.check_password('demo1122'))
        # a user for every role
        self.assertEqual(User.objects.filter(email__endswith='@sehatyar.online').count(), 9)

        # data present in every module
        self.assertTrue(Patient.objects.exists())
        self.assertTrue(Medicine.objects.exists())
        self.assertTrue(Sale.objects.exists())
        self.assertTrue(Department.objects.exists())
        self.assertTrue(Doctor.objects.filter(pmdc_no__startswith='DEMO-').exists())
        self.assertTrue(Appointment.objects.exists())
        self.assertTrue(Prescription.objects.exists())
        self.assertTrue(TestOrder.objects.exists())
        self.assertTrue(ImagingStudy.objects.exists())
        self.assertTrue(Admission.objects.exists())
        self.assertTrue(VitalsObservation.objects.exists())
        self.assertTrue(ShiftHandover.objects.exists())
        self.assertTrue(SurgeryRecord.objects.exists())
        self.assertTrue(Invoice.objects.exists())
        self.assertTrue(Expense.objects.exists())

        # everything is stamped to the demo hospital
        self.assertTrue(all(p.hospital_id == demo.id for p in Patient.objects.all()))
        self.assertTrue(all(a.hospital_id == demo.id for a in Admission.objects.all()))

    def test_re_running_is_idempotent(self):
        call_command('seed_public_demo')
        from patients.models import Patient
        demo = Hospital.objects.get(slug='demo')
        set_current_hospital(demo)
        n = Patient.objects.count()
        call_command('seed_public_demo')      # second run must not duplicate
        self.assertEqual(Patient.objects.count(), n)
        self.assertEqual(Hospital.objects.filter(slug='demo').count(), 1)
