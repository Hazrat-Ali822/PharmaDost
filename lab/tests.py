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
