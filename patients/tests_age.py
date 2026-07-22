"""Date of birth and age keep each other honest."""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital


class AgeFromDobTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))

    def tearDown(self):
        clear_current_hospital()

    def test_entering_a_date_of_birth_fills_the_age(self):
        born = timezone.localdate() - timedelta(days=365 * 30 + 8)   # ~30, birthday passed
        p = Patient.objects.create(full_name='Aged', dob=born, hospital=self.h)
        self.assertEqual(p.age_years, 30)
        self.assertEqual(p.current_age, 30)

    def test_a_birthday_still_to_come_this_year_does_not_count(self):
        today = timezone.localdate()
        # born 30 years ago but tomorrow -> still 29
        try:
            born = today.replace(year=today.year - 30) + timedelta(days=1)
        except ValueError:                                   # 29 Feb
            born = today.replace(year=today.year - 30, day=28) + timedelta(days=1)
        p = Patient.objects.create(full_name='Almost', dob=born, hospital=self.h)
        self.assertEqual(p.age_years, 29)

    def test_the_date_of_birth_wins_over_a_typed_age(self):
        born = timezone.localdate() - timedelta(days=365 * 40 + 10)
        p = Patient.objects.create(full_name='Mismatch', dob=born, age_years=5,
                                   hospital=self.h)
        self.assertEqual(p.age_years, 40)

    def test_a_stored_age_does_not_go_stale_when_a_date_of_birth_exists(self):
        born = timezone.localdate() - timedelta(days=365 * 30 + 8)
        p = Patient.objects.create(full_name='Fresh', dob=born, hospital=self.h)
        # simulate the row having been written years ago
        Patient.objects.filter(pk=p.pk).update(age_years=25)
        p.refresh_from_db()
        self.assertEqual(p.age_years, 25)                    # what is stored
        self.assertEqual(p.current_age, 30)                  # what is shown

    def test_an_age_alone_is_kept_and_no_date_of_birth_is_invented(self):
        """A made-up date on a medical record looks like fact. The form offers one
        the user can see and edit; the server never fabricates it."""
        p = Patient.objects.create(full_name='Guessed', age_years=45, hospital=self.h)
        self.assertIsNone(p.dob)
        self.assertEqual(p.current_age, 45)

    def test_a_patient_with_neither_has_no_age(self):
        p = Patient.objects.create(full_name='Unknown', hospital=self.h)
        self.assertIsNone(p.current_age)

    def test_age_on_never_returns_a_negative(self):
        future = timezone.localdate() + timedelta(days=400)
        self.assertEqual(Patient.age_on(future), 0)


class AgeFormTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))
        self.user = User.objects.create_user(email='r@r.com', password='pw',
                                             role='RECEPTIONIST', hospital=self.h)
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        clear_current_hospital()

    def test_registering_with_a_date_of_birth_stores_the_age(self):
        born = timezone.localdate() - timedelta(days=365 * 22 + 6)
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Dob Entry', 'gender': 'M',
            'dob': born.isoformat(), 'age_years': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Patient.objects.get(full_name='Dob Entry').age_years, 22)

    def test_registering_with_only_an_age_works(self):
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Age Entry', 'gender': 'F', 'age_years': '35',
        })
        self.assertEqual(resp.status_code, 302)
        p = Patient.objects.get(full_name='Age Entry')
        self.assertEqual(p.age_years, 35)
        self.assertEqual(p.current_age, 35)

    def test_the_form_carries_the_script_that_links_the_two_fields(self):
        resp = self.client.get(reverse('patient_add'))
        self.assertContains(resp, "id_age_years")
        self.assertContains(resp, "id_dob")
