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
        self.assertContains(resp, "id_age_months")
        self.assertContains(resp, "id_age_days")

    def test_the_date_of_birth_is_entered_as_day_month_year(self):
        """A native date box renders in the browser's locale, so the same record
        would read 29/01/2002 at one desk and 01/29/2002 at another."""
        resp = self.client.get(reverse('patient_add'))
        self.assertContains(resp, 'DD/MM/YYYY')
        self.assertContains(resp, 'dob-pick-btn')      # calendar still available

    def test_a_day_month_year_date_is_accepted(self):
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Typed Date', 'gender': 'M',
            'dob': '29/01/2002',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Patient.objects.get(full_name='Typed Date').dob,
                         date(2002, 1, 29))

    def test_an_iso_date_from_the_calendar_still_parses(self):
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Picked Date', 'gender': 'F',
            'dob': '2002-01-29',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Patient.objects.get(full_name='Picked Date').dob,
                         date(2002, 1, 29))

    def test_an_existing_date_is_shown_back_as_day_month_year(self):
        p = Patient.objects.create(full_name='Editable', dob=date(2002, 1, 29),
                                   hospital=self.h)
        resp = self.client.get(reverse('patient_edit', args=[p.pk]))
        self.assertContains(resp, '29/01/2002')

    def test_the_form_is_rendered_exactly_once(self):
        """A multi-line `{# #}` is not a comment in Django — it prints, and runs
        any tag inside it. One holding `{{ form.as_p }}` rendered the whole form a
        second time, above the real one."""
        body = self.client.get(reverse('patient_add')).content.decode()
        for field in ('full_name', 'cnic', 'age_months'):
            self.assertEqual(body.count(f'name="{field}"'), 1,
                             f'{field} appears more than once — the form is rendered twice')

    def test_the_age_boxes_sit_next_to_the_date_not_at_the_bottom(self):
        """Declared form fields land after the model's unless ordered, which put
        Months and Days below Allergies."""
        body = self.client.get(reverse('patient_add')).content.decode()
        self.assertLess(body.index('id_age_months'), body.index('id_allergies'))
        self.assertLess(body.index('id_age_days'), body.index('id_allergies'))

    def test_the_reception_visit_screen_gets_the_same_fields_and_script(self):
        """The visit screen used to render this form with `as_p` and no script,
        so CNIC dashes and the age boxes did nothing at the front desk."""
        resp = self.client.get(reverse('visit_create'))
        self.assertContains(resp, 'id_age_months')
        self.assertContains(resp, 'DD/MM/YYYY')
        self.assertContains(resp, 'dob-pick-btn')

    def test_months_and_days_become_a_real_date_of_birth(self):
        """A months/days entry is day-precise, so deriving the date is arithmetic
        on what reception said — not a guess."""
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Baby Ali', 'gender': 'M',
            'age_years': '0', 'age_months': '7', 'age_days': '3',
        })
        self.assertEqual(resp.status_code, 302)
        baby = Patient.objects.get(full_name='Baby Ali')
        self.assertIsNotNone(baby.dob)
        self.assertEqual(baby.age_parts, (0, 7, 3))
        self.assertEqual(baby.age_display, '7m 3d')

    def test_years_alone_still_leaves_the_date_of_birth_blank(self):
        resp = self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Rounded', 'gender': 'M', 'age_years': '35',
        })
        self.assertEqual(resp.status_code, 302)
        p = Patient.objects.get(full_name='Rounded')
        self.assertIsNone(p.dob)
        self.assertEqual(p.age_display, '35y')

    def test_a_real_date_of_birth_beats_typed_months_and_days(self):
        born = timezone.localdate() - timedelta(days=365 * 22 + 6)
        self.client.post(reverse('patient_add'), {
            'mrn': '', 'full_name': 'Has A Date', 'gender': 'F',
            'dob': born.isoformat(),
            'age_years': '0', 'age_months': '3', 'age_days': '9',
        })
        p = Patient.objects.get(full_name='Has A Date')
        self.assertEqual(p.dob, born)
        self.assertEqual(p.age_parts[0], 22)


class AgeDisplayTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='Shaheen General Hospital', slug='sgh',
                                         expiry_date=date.today() + timedelta(days=30))

    def tearDown(self):
        clear_current_hospital()

    def _born(self, years=0, months=0, days=0):
        """A patient whose age today is exactly the parts given."""
        import calendar as cal
        today = timezone.localdate()
        total = years * 12 + months
        year, month = divmod(today.month - 1 - total, 12)
        year, month = today.year + year, month + 1
        day = min(today.day, cal.monthrange(year, month)[1])
        born = date(year, month, day) - timedelta(days=days)
        return Patient.objects.create(full_name='X', dob=born, hospital=self.h)

    def test_zero_parts_are_dropped(self):
        self.assertEqual(self._born(years=34, months=5, days=12).age_display, '34y 5m 12d')
        self.assertEqual(self._born(years=34).age_display, '34y')
        self.assertEqual(self._born(months=7, days=3).age_display, '7m 3d')
        self.assertEqual(self._born(days=4).age_display, '4d')

    def test_a_baby_born_today_reads_as_a_newborn(self):
        p = Patient.objects.create(full_name='Today', dob=timezone.localdate(),
                                   hospital=self.h)
        self.assertEqual(p.age_display, 'Newborn')

    def test_a_typed_age_with_no_date_shows_years_only(self):
        p = Patient.objects.create(full_name='Typed', age_years=42, hospital=self.h)
        self.assertEqual(p.age_display, '42y')

    def test_nothing_on_file_shows_nothing(self):
        p = Patient.objects.create(full_name='Unknown', hospital=self.h)
        self.assertEqual(p.age_display, '')

    def test_days_borrow_from_the_month_that_actually_precedes_today(self):
        """A flat 30-day borrow makes a baby read a day older than it is in the
        months that follow a short one."""
        parts = Patient.age_parts_on(date(2026, 1, 31), on=date(2026, 3, 1))
        self.assertEqual(parts, (0, 1, 1))       # 31 Jan -> 28 Feb is one month

    def test_a_future_date_of_birth_never_goes_negative(self):
        future = timezone.localdate() + timedelta(days=30)
        self.assertEqual(Patient.age_parts_on(future), (0, 0, 0))
