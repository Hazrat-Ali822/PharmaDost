from decimal import Decimal
from django.test import TestCase
from accounts.models import User
from patients.models import Patient
from lab.models import LabTest, TestCategory, TestOrder, TestResult


class LabFlowTests(TestCase):
    def test_lab_order_and_result_flow(self):
        category = TestCategory.objects.create(name='Biochemistry')
        lab_test = LabTest.objects.create(category=category, name='CBC', price=Decimal('250.00'))
        patient = Patient.objects.create(mrn='MRN-100', full_name='Lab Patient', phone='111')
        user = User.objects.create_user(email='lab@example.com', password='pass1234')

        order = TestOrder.objects.create(patient=patient, ordered_by=user)
        result = TestResult.objects.create(test_order=order, lab_test=lab_test, result_value='5.2', remarks='Normal')

        self.assertEqual(order.status, 'Pending')
        self.assertEqual(order.results.count(), 1)
        self.assertEqual(result.lab_test.name, 'CBC')


class DedupeCatalogueTest(TestCase):
    """Reported from the live demo: the ordering screen offered the same test four
    times, and the copies were not identical — one had a unit and a reference
    range, the others were blank, so which one a doctor happened to tick decided
    whether the printed report carried a normal range."""

    def setUp(self):
        from decimal import Decimal
        from saas.models import Hospital
        from lab.models import TestCategory, LabTest, TestOrder, TestResult
        from patients.models import Patient
        from datetime import date, timedelta
        self.h = Hospital.objects.create(
            name='Dedupe H', slug='dedupe-h',
            expiry_date=date.today() + timedelta(days=365))
        cat = TestCategory.all_objects.create(name='Haematology', hospital=self.h)
        # The blank one first, so "keep the oldest" would pick the wrong row.
        self.blank = LabTest.all_objects.create(category=cat, name='CBC',
                                                hospital=self.h)
        self.full = LabTest.all_objects.create(
            category=cat, name='CBC', unit='g/dL', normal_range='12-16',
            price=Decimal('450'), hospital=self.h)
        self.dupe3 = LabTest.all_objects.create(category=cat, name='cbc',
                                                hospital=self.h)
        p = Patient.objects.create(full_name='Dedupe Patient', gender='M',
                                   hospital=self.h)
        order = TestOrder.objects.create(patient=p)
        self.result = TestResult.objects.create(test_order=order,
                                                lab_test=self.blank,
                                                result_value='13.1')

    def test_it_keeps_the_best_populated_row_and_saves_the_history(self):
        from django.core.management import call_command
        from lab.models import LabTest
        call_command('dedupe_catalogue', hospital='dedupe-h', verbosity=0)

        remaining = LabTest.all_objects.filter(hospital=self.h, name__iexact='CBC')
        self.assertEqual(remaining.count(), 1, 'duplicates were not merged')
        survivor = remaining.first()
        self.assertEqual(survivor.pk, self.full.pk,
                         'merged into the blank row and lost the reference range')

        # The entered result must survive and follow the survivor. Deleting a
        # LabTest cascades to its TestResults, so getting the order wrong here
        # erases results rather than merging them.
        self.result.refresh_from_db()
        self.assertEqual(self.result.lab_test_id, self.full.pk)
        self.assertEqual(self.result.result_value, '13.1')

    def test_dry_run_changes_nothing(self):
        from django.core.management import call_command
        from lab.models import LabTest
        call_command('dedupe_catalogue', hospital='dedupe-h', dry_run=True,
                     verbosity=0)
        self.assertEqual(
            LabTest.all_objects.filter(hospital=self.h, name__iexact='CBC').count(), 3)
