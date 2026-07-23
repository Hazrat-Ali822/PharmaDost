"""What the owner can see — and what they must not.

Two separate concerns share this file because they are two halves of the same
question: the admin should know everything about THEIR hospital, and nothing
about anyone else's.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification, User
from audit.models import AuditLog
from billing.models import Invoice
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital


class AuditLogIsolationTest(TestCase):
    """The audit trail is the most sensitive page in the product — every patient
    name, sale and sign-in. It used to be completely unscoped."""

    def setUp(self):
        self.h1 = Hospital.objects.create(name='Shaheen', slug='sgh',
                                          expiry_date=date.today() + timedelta(days=30))
        self.h2 = Hospital.objects.create(name='Gull', slug='gull',
                                          expiry_date=date.today() + timedelta(days=30))
        self.admin1 = User.objects.create_user(email='a1@x.com', password='pw',
                                               role='ADMIN', hospital=self.h1)
        self.admin2 = User.objects.create_user(email='a2@x.com', password='pw',
                                               role='ADMIN', hospital=self.h2)
        self.client = Client()
        self.client.force_login(self.admin1)

    def tearDown(self):
        clear_current_hospital()

    def test_one_admin_cannot_read_another_hospitals_trail(self):
        AuditLog.objects.create(user=self.admin2, action='CREATE', model_name='Patient',
                                object_repr='RIVAL PATIENT', hospital=self.h2)
        AuditLog.objects.create(user=self.admin1, action='CREATE', model_name='Patient',
                                object_repr='OUR PATIENT', hospital=self.h1)
        body = self.client.get(reverse('audit_log')).content.decode()
        self.assertIn('OUR PATIENT', body)
        self.assertNotIn('RIVAL PATIENT', body)

    def test_the_filter_dropdown_does_not_name_another_hospitals_staff(self):
        """A filter list leaks just as surely as the rows would."""
        AuditLog.objects.create(user=self.admin2, action='LOGIN', model_name='User',
                                object_repr='x', hospital=self.h2)
        body = self.client.get(reverse('audit_log')).content.decode()
        self.assertNotIn('a2@x.com', body)

    def test_activity_is_filed_under_the_hospital_it_concerns(self):
        self.client.post(reverse('patient_add'),
                         {'mrn': '', 'full_name': 'Filed Right', 'gender': 'M'})
        entry = AuditLog.all_objects.filter(model_name='Patient').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.hospital, self.h1)

    def test_a_failed_sign_in_is_filed_against_the_staff_members_hospital(self):
        """Otherwise the admin whose account is being guessed at never sees it."""
        Client().post(reverse('login'), {'username': 'a1@x.com', 'password': 'wrong'})
        entry = AuditLog.all_objects.filter(action='LOGIN_FAILED').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.hospital, self.h1)


class AdminOverviewTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.admin = User.objects.create_user(email='a@x.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def test_the_dashboard_reports_todays_numbers(self):
        Patient.objects.create(full_name='Walk In', hospital=self.h)
        resp = self.client.get(reverse('user_mgmt:admin_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Patients registered')
        self.assertEqual(resp.context['today']['patients_registered'], 1)

    def test_unpaid_bills_are_surfaced_as_something_needing_attention(self):
        patient = Patient.objects.create(full_name='Owes Money', hospital=self.h)
        Invoice.objects.create(patient=patient, total=Decimal('500'),
                               paid=Decimal('0'), created_by=self.admin,
                               hospital=self.h)
        resp = self.client.get(reverse('user_mgmt:admin_dashboard'))
        labels = [i['label'] for i in resp.context['attention']]
        self.assertIn('unpaid invoice(s)', labels)

    def test_a_quiet_hospital_says_so_rather_than_listing_zeroes(self):
        resp = self.client.get(reverse('user_mgmt:admin_dashboard'))
        self.assertEqual(resp.context['attention'], [])
        self.assertContains(resp, 'Nothing needs attention')

    def test_the_overview_does_not_count_another_hospitals_work(self):
        other = Hospital.objects.create(name='Gull', slug='gull',
                                        expiry_date=date.today() + timedelta(days=30))
        Patient.objects.create(full_name='Theirs', mrn='OTH-1', hospital=other)
        resp = self.client.get(reverse('user_mgmt:admin_dashboard'))
        self.assertEqual(resp.context['today']['patients_registered'], 0)

    def test_recent_activity_is_this_hospitals_only(self):
        other = Hospital.objects.create(name='Gull', slug='gull',
                                        expiry_date=date.today() + timedelta(days=30))
        AuditLog.objects.create(user=None, action='CREATE', model_name='Sale',
                                object_repr='THEIR SALE', hospital=other)
        resp = self.client.get(reverse('user_mgmt:admin_dashboard'))
        self.assertNotContains(resp, 'THEIR SALE')


class AdminIsToldAboutTest(TestCase):
    """The exceptional events — the ones the owner cannot find out any other way."""

    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.admin = User.objects.create_user(email='a@x.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.pharmacist = User.objects.create_user(email='p@x.com', password='pw',
                                                   role='PHARMACIST', hospital=self.h)
        self.client = Client()

    def tearDown(self):
        clear_current_hospital()

    def _stocked_batch(self):
        from inventory.models import Medicine
        med = Medicine.objects.create(name='Panadol', price=Decimal('20'),
                                      expiry_date=date.today() + timedelta(days=365),
                                      hospital=self.h)
        med.add_stock(50, expiry_date=date.today() + timedelta(days=365),
                      cost_price=Decimal('12'))
        return med.batches.first()

    def test_stock_written_off_reaches_the_admin(self):
        """Stock leaving with no sale behind it is the shape shrinkage takes."""
        batch = self._stocked_batch()
        self.client.force_login(self.pharmacist)
        resp = self.client.post(reverse('adjustment_create'), {
            'batch': batch.pk, 'qty_change': '-10', 'reason': 'DAMAGE', 'notes': 'dropped',
        })
        self.assertEqual(resp.status_code, 302)
        note = Notification.objects.filter(user=self.admin).first()
        self.assertIsNotNone(note, 'the admin was not told about a write-off')
        self.assertIn('written off', note.message)
        self.assertIn('Panadol', note.message)

    def test_adding_stock_back_does_not_nag_the_admin(self):
        batch = self._stocked_batch()
        self.client.force_login(self.pharmacist)
        self.client.post(reverse('adjustment_create'), {
            'batch': batch.pk, 'qty_change': '5', 'reason': 'COUNT', 'notes': 'recount',
        })
        self.assertFalse(Notification.objects.filter(user=self.admin).exists())

    def test_voiding_a_bill_reaches_the_admin(self):
        patient = Patient.objects.create(full_name='Voided', hospital=self.h)
        invoice = Invoice.objects.create(patient=patient, total=Decimal('900'),
                                         created_by=self.admin, hospital=self.h)
        self.client.force_login(self.admin)
        self.client.post(reverse('invoice_void', args=[invoice.pk]))
        note = Notification.objects.filter(user=self.admin).first()
        self.assertIsNotNone(note)
        self.assertIn('voided', note.message)

    def test_a_run_of_failed_sign_ins_reaches_the_admin(self):
        c = Client()
        for _ in range(3):
            c.post(reverse('login'), {'username': 'a@x.com', 'password': 'wrong'})
        notes = Notification.objects.filter(user=self.admin, message__contains='failed sign-in')
        self.assertEqual(notes.count(), 1, 'should warn once, on the attempt that crosses the line')

    def test_a_single_mistyped_password_is_not_worth_a_notification(self):
        Client().post(reverse('login'), {'username': 'a@x.com', 'password': 'wrong'})
        self.assertFalse(
            Notification.objects.filter(user=self.admin, message__contains='failed sign-in').exists())

    def test_the_warning_stops_after_it_has_been_raised_once(self):
        c = Client()
        for _ in range(6):
            c.post(reverse('login'), {'username': 'a@x.com', 'password': 'wrong'})
        notes = Notification.objects.filter(user=self.admin, message__contains='failed sign-in')
        self.assertEqual(notes.count(), 1)
