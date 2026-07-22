"""Patient MRN numbering: automatic, per hospital, starting at 1."""
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from patients.services import derive_prefix, next_mrn
from saas.models import Hospital
from saas.utils import set_current_hospital, clear_current_hospital
from user_mgmt.models import SiteSettings


class DerivePrefixTest(TestCase):
    def test_multi_word_name_becomes_initials(self):
        self.assertEqual(derive_prefix('Shaheen General Hospital'), 'SGH')
        self.assertEqual(derive_prefix('Gull Pharmacy'), 'GP')

    def test_single_word_name_uses_leading_letters(self):
        self.assertEqual(derive_prefix('PharmaDost'), 'PHA')

    def test_prefix_is_capped_and_never_empty(self):
        self.assertLessEqual(len(derive_prefix('A B C D E F G H')), 6)
        self.assertEqual(derive_prefix(''), 'MRN')
        self.assertEqual(derive_prefix('!!! ???'), 'MRN')


class MrnAllocationTest(TestCase):
    def setUp(self):
        self.h1 = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                          expiry_date=date.today() + timedelta(days=30))
        self.h2 = Hospital.objects.create(name='Gull Pharmacy', slug='gull',
                                          expiry_date=date.today() + timedelta(days=30))

    def tearDown(self):
        clear_current_hospital()

    def test_numbers_start_at_one_and_count_up(self):
        a = Patient.objects.create(full_name='First', hospital=self.h1)
        b = Patient.objects.create(full_name='Second', hospital=self.h1)
        self.assertEqual(a.mrn, 'SGH-000001')
        self.assertEqual(b.mrn, 'SGH-000002')

    def test_each_hospital_has_its_own_sequence(self):
        a = Patient.objects.create(full_name='At Shaheen', hospital=self.h1)
        b = Patient.objects.create(full_name='At Gull', hospital=self.h2)
        self.assertEqual(a.mrn, 'SGH-000001')
        self.assertEqual(b.mrn, 'GP-000001')      # both are number 1, no clash

    def test_the_same_mrn_in_two_hospitals_is_allowed(self):
        Patient.objects.create(full_name='A', mrn='X-000001', hospital=self.h1)
        Patient.objects.create(full_name='B', mrn='X-000001', hospital=self.h2)
        # each tenant sees exactly its own patient behind that number
        set_current_hospital(self.h1)
        self.assertEqual(Patient.objects.get(mrn='X-000001').full_name, 'A')
        set_current_hospital(self.h2)
        self.assertEqual(Patient.objects.get(mrn='X-000001').full_name, 'B')

    def test_the_same_mrn_twice_in_one_hospital_is_rejected(self):
        Patient.objects.create(full_name='A', mrn='X-000001', hospital=self.h1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Patient.objects.create(full_name='B', mrn='X-000001', hospital=self.h1)

    def test_a_single_site_install_still_numbers_patients(self):
        """No hospital row at all - the desktop build and a fresh install."""
        a = Patient.objects.create(full_name='Solo')
        self.assertTrue(a.mrn, 'a hospital-less patient must still get an MRN')
        b = Patient.objects.create(full_name='Solo Two')
        self.assertNotEqual(a.mrn, b.mrn)

    def test_an_explicit_mrn_is_kept_untouched(self):
        p = Patient.objects.create(full_name='Migrated', mrn='OLD-777', hospital=self.h1)
        self.assertEqual(p.mrn, 'OLD-777')
        # and it does not consume a number from the sequence
        nxt = Patient.objects.create(full_name='Next', hospital=self.h1)
        self.assertEqual(nxt.mrn, 'SGH-000001')

    def test_editing_a_patient_does_not_renumber_them(self):
        p = Patient.objects.create(full_name='Stable', hospital=self.h1)
        original = p.mrn
        p.full_name = 'Stable Renamed'
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.mrn, original)

    def test_admin_can_change_the_prefix_without_touching_old_cards(self):
        old = Patient.objects.create(full_name='Before', hospital=self.h1)
        row = SiteSettings.objects.get(hospital=self.h1)
        row.mrn_prefix = 'NEW'
        row.save()
        new = Patient.objects.create(full_name='After', hospital=self.h1)
        self.assertEqual(old.mrn, 'SGH-000001')
        self.assertEqual(new.mrn, 'NEW-000002')

    def test_numbers_are_zero_padded_so_they_sort_in_issue_order(self):
        row = SiteSettings.objects.create(hospital=self.h1, brand_name=self.h1.name,
                                          mrn_last_number=8)
        self.assertEqual(next_mrn(self.h1), 'SGH-000009')
        row.refresh_from_db()
        self.assertEqual(row.mrn_last_number, 9)


class MrnRegistrationFormTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(email='r@r.com', password='pw',
                                             role='RECEPTIONIST', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def test_the_form_shows_the_next_number_and_does_not_demand_one(self):
        resp = self.client.get(reverse('patient_add'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SGH-000001')

    def test_registering_without_an_mrn_allocates_one(self):
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Walk In', 'gender': 'M',
        })
        self.assertEqual(resp.status_code, 302)
        patient = Patient.objects.get(full_name='Walk In')
        self.assertEqual(patient.mrn, 'SGH-000001')
        self.assertEqual(patient.hospital, self.h)

    def test_a_duplicate_mrn_is_refused_with_a_message(self):
        Patient.objects.create(full_name='Existing', mrn='SGH-000900', hospital=self.h)
        resp = self.client.post(reverse('patient_add'), {
            'mrn': 'SGH-000900', 'full_name': 'Clash', 'gender': 'M',
        })
        self.assertEqual(resp.status_code, 200)          # form re-rendered
        self.assertContains(resp, 'already has this MRN')
        self.assertFalse(Patient.objects.filter(full_name='Clash').exists())
