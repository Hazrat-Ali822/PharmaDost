"""Second batch of the 18 August QA pass — data correctness and honesty.

Each class here is one defect that a browser agent found by using the product,
and each is the kind that a passing test suite happily coexists with, because
the server was doing something defensible and the *user* was misled.

    python manage.py test tests.test_qa_defects_b --settings=pharma_mgmt.test_settings
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase

from accounts.models import NO_LOGIN_ROLE, User
from patients.models import Patient
from prescriptions.dosing import dispense_quantity, doses_per_day
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=900)


class TenantCase(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='QA Hospital', slug='qa-h',
                                         expiry_date=_future())
        self.admin = User.objects.create_user(
            email='admin@qa.com', password='pw', role='ADMIN', hospital=self.h)
        set_current_hospital(self.h)
        self.c = Client()
        self.assertTrue(self.c.login(email='admin@qa.com', password='pw'))

    def tearDown(self):
        clear_current_hospital()


class RejectedFormSaysSoTest(TenantCase):
    """#5 — a negative price was reported as "silently fails, no error shown".

    The server was right all along: `MinValueValidator` rejected it and the
    bound form came back with the message and the typed values intact. But
    `.errorlist` had **no CSS rule anywhere in app.css**, so on screen the
    rejection was a small grey bulleted line in body text above a filled-in
    field — invisible enough that a careful tester concluded the save had
    silently done nothing. An error nobody can see is worse than no error: the
    user believes the record was created.
    """

    def test_the_error_is_returned_and_the_typing_is_kept(self):
        r = self.c.post('/medicines/add/', {
            'name': 'QA Syrup', 'price': '-1', 'quantity': '3',
            'reorder_level': '10', 'units_per_pack': '1', 'wholesale_price': '0'})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('errorlist', html)
        self.assertIn('greater than or equal to 0', html)
        self.assertIn('QA Syrup', html, 'the form came back empty')

    def test_the_error_list_is_actually_styled(self):
        """The whole defect. If this file stops styling `.errorlist`, every
        validation message in the app goes back to being invisible."""
        from pathlib import Path

        from django.conf import settings
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'app.css').read_text(
            encoding='utf-8')
        self.assertRegex(css, r'\.errorlist\s*\{',
                         'app.css no longer styles Django form errors')
        block = css.split('.errorlist')[1].split('}')[0]
        self.assertIn('background', block)
        self.assertIn('color', block)


class IdentityFieldsAreValidatedTest(TenantCase):
    """#15 — `hello world` in CNIC and `not-a-phone` in phone both saved."""

    def test_a_cnic_with_no_digits_is_refused_not_silently_dropped(self):
        r = self.c.post('/patients/add/', {
            'full_name': 'Test Patient', 'gender': 'M', 'cnic': 'hello world'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('no digits', r.content.decode())
        self.assertFalse(Patient.objects.filter(full_name='Test Patient').exists())

    def test_a_phone_that_is_not_a_number_is_refused(self):
        r = self.c.post('/patients/add/', {
            'full_name': 'Test Patient', 'gender': 'M', 'phone': 'not-a-phone'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('does not look like a phone number', r.content.decode())

    def test_a_pakistani_mobile_is_stored_in_one_shape(self):
        """Numbers were stored however they were typed, which is what made the
        search depend on who registered the patient."""
        self.c.post('/patients/add/', {
            'full_name': 'Shaped Patient', 'gender': 'F', 'phone': '03009876543'})
        p = Patient.objects.get(full_name='Shaped Patient')
        self.assertEqual(p.phone, '0300-9876543')

    def test_a_blank_cnic_is_still_fine(self):
        self.c.post('/patients/add/', {'full_name': 'No Cnic', 'gender': 'M'})
        self.assertTrue(Patient.objects.filter(full_name='No Cnic').exists())


class PatientSearchFindsThemTest(TenantCase):
    """#14 — an undashed phone found nothing, and so did first name + surname.

    Both the registry and the reception desk are checked, because they had
    separate implementations that disagreed.
    """
    URLS = ('/patients/?q=', '/opd/reception/?q=')

    def setUp(self):
        super().setUp()
        Patient.objects.create(full_name='Ayesha Bibi Qadir', hospital=self.h,
                               phone='0300-9876543', cnic='35202-1234567-1',
                               gender='F')

    def _finds(self, query):
        return [q for q in self.URLS
                if 'Ayesha Bibi Qadir' in
                self.c.get(q + query, follow=True).content.decode()]

    def test_a_phone_typed_without_dashes(self):
        self.assertEqual(len(self._finds('03009876543')), 2)

    def test_first_name_and_surname_skipping_the_middle_name(self):
        self.assertEqual(len(self._finds('ayesha qadir')), 2)

    def test_the_words_in_any_order(self):
        self.assertEqual(len(self._finds('qadir ayesha')), 2)

    def test_a_cnic_straight_off_the_card(self):
        self.assertEqual(len(self._finds('3520212345671')), 2)

    def test_it_still_says_no_when_there_is_no_match(self):
        self.assertEqual(self._finds('zzzznobody'), [])

    def test_two_words_are_ANDed_not_ORed(self):
        """"ali khan" must not return every Ali and every Khan."""
        Patient.objects.create(full_name='Ali Raza', hospital=self.h, gender='M')
        Patient.objects.create(full_name='Imran Khan', hospital=self.h, gender='M')
        Patient.objects.create(full_name='Ali Khan', hospital=self.h, gender='M')
        html = self.c.get('/patients/?q=ali khan', follow=True).content.decode()
        self.assertIn('Ali Khan', html)
        self.assertNotIn('Ali Raza', html)
        self.assertNotIn('Imran Khan', html)


class DispensingQuantityTest(TestCase):
    """#9 — the POS took the quantity from the number of DAYS.

    It understood one shape, `1+0+1`, and fell back to `duration_days` for
    everything else — so "1 tab TDS x 5 days" (15 tablets) loaded as 5. That is
    not merely wrong, it is *plausibly* wrong: a small number in a quantity box
    that nobody re-checks, and short-dispensing a course of antibiotics is a
    clinical error.
    """

    def test_the_shapes_a_doctor_actually_writes(self):
        cases = [
            ('1 tab TDS', 5, 15),
            ('1 tab BD', 7, 14),
            ('1 tab OD', 10, 10),
            ('TDS', 5, 15),
            ('2 tsp BD', 3, 12),
            ('5ml QID', 2, 40),
            ('1 tab q8h', 3, 9),
        ]
        for dosage, days, expected in cases:
            with self.subTest(dosage=dosage):
                self.assertEqual(dispense_quantity(dosage, days), expected)

    def test_the_slotted_form_still_works(self):
        self.assertEqual(dispense_quantity('1+0+1', 5), 10)
        self.assertEqual(dispense_quantity('1-0-1', 5), 10)

    def test_half_a_tablet_rounds_up_never_down(self):
        """Half a tablet a day for 5 days is 3 handed over, not 2."""
        self.assertEqual(dispense_quantity('1/2 tab OD', 5), 3)

    def test_as_needed_refuses_to_guess(self):
        """PRN/SOS has no quantity. Inventing one is how somebody goes home with
        thirty of something they were meant to take when it hurts."""
        self.assertIsNone(dispense_quantity('1 tab SOS', 5))
        self.assertIsNone(dispense_quantity('2 tabs PRN', 5))

    def test_it_gives_up_rather_than_inventing(self):
        self.assertIsNone(doses_per_day('as directed'))
        self.assertIsNone(doses_per_day(''))
        self.assertIsNone(doses_per_day(None))


class PayrollOnlyStaffAreNotPharmacistsTest(TenantCase):
    """#12/#13/#37 — a ward boy added without a login appeared everywhere as a
    Pharmacist, with a generated address printed beside his name as though it
    were real, and an avatar initial taken from that address.

    The `User` row is still created (Attendance, LeaveRequest and SalaryPayment
    all point at one), but nothing about it is now presented as a real account.
    """

    def _add_ward_boy(self):
        self.c.post('/hr/staff/add/', {
            'full_name': 'Rashid Mehmood', 'designation': 'Ward Boy',
            'monthly_salary': '25000'})
        return User.objects.get(first_name='Rashid')

    def test_he_is_not_given_a_real_role(self):
        u = self._add_ward_boy()
        self.assertEqual(u.role, NO_LOGIN_ROLE)
        self.assertNotEqual(u.role, 'PHARMACIST')
        self.assertEqual(u.get_role_display(), 'No system access')

    def test_the_generated_address_is_never_shown(self):
        u = self._add_ward_boy()
        self.assertTrue(u.email.endswith('@no-login.invalid'))
        self.assertEqual(u.display_email, '')
        self.assertEqual(u.display_name, 'Rashid Mehmood')
        self.assertFalse(u.signs_in)
        for url in ('/hr/', '/manage/users/'):
            html = self.c.get(url, follow=True).content.decode()
            self.assertNotIn('no-login.invalid', html, f'leaked on {url}')

    def test_he_cannot_sign_in_three_ways_over(self):
        u = self._add_ward_boy()
        self.assertFalse(u.has_usable_password())
        self.assertEqual(u.custom_features, [])
        self.assertEqual(u.effective_features(), set())

    def test_the_avatar_initial_comes_from_the_name(self):
        u = self._add_ward_boy()
        self.assertEqual(u.initials, 'RM')

    def test_a_demo_style_address_no_longer_collapses_everyone_to_one_letter(self):
        """Five demo staff all showed "D", because every address began `demo.`"""
        a = User.objects.create_user(email='demo.nurse@x.com', password='pw',
                                     role='NURSE', hospital=self.h,
                                     first_name='Ayesha', last_name='Nurse')
        b = User.objects.create_user(email='demo.labtech@x.com', password='pw',
                                     role='LABTECH', hospital=self.h,
                                     first_name='Kamran', last_name='Lab')
        self.assertEqual((a.initials, b.initials), ('AN', 'KL'))

    def test_payroll_lists_him_by_name_and_job(self):
        self._add_ward_boy()
        html = self.c.get('/hr/salary/new/', follow=True).content.decode()
        self.assertIn('Rashid Mehmood', html)
        self.assertIn('Ward Boy', html)
        self.assertNotIn('no-login.invalid', html)


class PrescriptionStatusIsVisibleTest(TenantCase):
    """#10 — a dispensed prescription showed no sign of it, and the red
    "Declined" control in each row read exactly like a per-row status badge, so
    after a successful sale the page appeared to say every medicine had been
    refused."""

    def _prescription(self):
        from opd.models import Appointment, Department, Doctor
        from prescriptions.models import Prescription, PrescriptionItem
        p = Patient.objects.create(full_name='Rx Patient', hospital=self.h,
                                   gender='M')
        dep = Department.objects.create(name='Medicine')
        doc = Doctor.objects.create(full_name='Dr Test', department=dep,
                                    pmdc_no='T-1')
        appt = Appointment.objects.create(patient=p, doctor=doc,
                                          appointment_date=date.today())
        rx = Prescription.objects.create(appointment=appt)
        PrescriptionItem.objects.create(prescription=rx,
                                        custom_medicine_name='Panadol',
                                        dosage='1 tab TDS', duration_days=5)
        return rx

    def test_a_pending_prescription_says_so(self):
        rx = self._prescription()
        html = self.c.get(f'/prescriptions/{rx.pk}/').content.decode()
        self.assertIn('Waiting at the pharmacy', html)

    def test_a_dispensed_prescription_says_so(self):
        rx = self._prescription()
        rx.status = 'DISPENSED'
        rx.save(update_fields=['status'])
        html = self.c.get(f'/prescriptions/{rx.pk}/').content.decode()
        self.assertIn('Dispensed', html)

    def test_the_decline_control_no_longer_reads_as_a_status(self):
        rx = self._prescription()
        html = self.c.get(f'/prescriptions/{rx.pk}/').content.decode()
        self.assertIn('Mark declined', html)
        self.assertNotIn('>Declined<', html)


class WholesaleCannotReadPrescriptionsTest(TenantCase):
    """Role audit — `pos` reaches a prescription so the pharmacist can mark a
    line declined, and WHOLESALE also holds `pos`. That counter sells to other
    shops, has no patients, and a named patient's prescribed medicines with
    their doctor is a medical record."""

    def test_the_pharmacist_still_gets_in(self):
        from accounts.permissions import can_handle_prescriptions
        u = User.objects.create_user(email='ph@qa.com', password='pw',
                                     role='PHARMACIST', hospital=self.h)
        self.assertTrue(can_handle_prescriptions(u))

    def test_the_wholesale_counter_does_not(self):
        from accounts.permissions import can_handle_prescriptions
        u = User.objects.create_user(email='wh@qa.com', password='pw',
                                     role='WHOLESALE', hospital=self.h)
        self.assertFalse(can_handle_prescriptions(u))

    def test_and_the_till_does_not_offer_them_the_panel(self):
        User.objects.create_user(email='wh2@qa.com', password='pw',
                                 role='WHOLESALE', hospital=self.h)
        c = Client()
        self.assertTrue(c.login(email='wh2@qa.com', password='pw'))
        html = c.get('/sales/new/', follow=True).content.decode()
        self.assertNotIn('Load Pending Doctor Prescriptions', html)


class VitalsAreWithinPhysiologyTest(TenantCase):
    """#15 — SpO2 500%, pain 99/10 and temperature 999 F all saved.

    The bounds are deliberately wide: this is not clinical judgement, it is a
    check that the number came from a patient at all. A nonsense vital is worse
    than a missing one, because `compute_mews` then scores it and the nursing
    board sorts a ward by the result.
    """

    def _form(self, **data):
        from django.utils import timezone

        from ipd.forms import VitalsObservationForm
        data.setdefault('taken_at',
                        timezone.localtime().strftime('%Y-%m-%dT%H:%M'))
        data.setdefault('consciousness', 'A')
        return VitalsObservationForm(data=data)

    def test_impossible_values_are_refused(self):
        for field, value in [('spo2', 500), ('pain_score', 99),
                             ('temperature', 999), ('pulse', 5000),
                             ('respiratory_rate', 400)]:
            with self.subTest(field=field):
                form = self._form(**{field: value})
                self.assertFalse(form.is_valid(), f'{field}={value} was accepted')
                self.assertIn(field, form.errors)

    def test_a_real_set_of_vitals_still_saves(self):
        form = self._form(temperature='99.4', pulse='88', respiratory_rate='18',
                          systolic_bp='120', diastolic_bp='80', spo2='97',
                          pain_score='3', consciousness='A')
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_a_genuinely_sick_patient_is_not_blocked(self):
        """The point of the ward is the abnormal. A tachycardic, hypoxic,
        febrile patient must still be chartable."""
        form = self._form(temperature='105.8', pulse='170', respiratory_rate='34',
                          systolic_bp='72', diastolic_bp='40', spo2='79',
                          consciousness='V')
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_a_partial_observation_is_still_allowed(self):
        self.assertTrue(self._form(pulse='80').is_valid())

    def test_but_an_empty_one_is_not(self):
        self.assertFalse(self._form().is_valid())


class NoRawDjangoEmptyOptionTest(TenantCase):
    """#35, second half — `---------` as the first choice in a dropdown.

    It survived the first fix pass because it arrives by **two** different
    routes and only one had been dealt with: a foreign key uses
    `ModelChoiceField.empty_label`, while a model field with `choices` and
    `blank=True` gets `BLANK_CHOICE_DASH` prepended by the model layer. Add
    Medicine has one of each — Supplier and Category — so the screen still
    showed the hyphens after the ModelChoiceField half was fixed.

    Neither can be reached from a template, and setting them per field would be
    a hundred `__init__`s that each have to remember, so both are handled once
    in `accounts.templatetags.form_extras.friendly_empty_labels`, applied by
    `partials/_form.html`.
    """
    # One screen per rendering path, not an exhaustive list.
    # One per rendering path: the shared renderer, and the hand-rolled field
    # templates that never pass through it (patients/_fields.html and friends),
    # which is exactly where the first fix missed.
    PAGES = ['/medicines/add/', '/opd/departments/', '/suppliers/add/',
             '/patients/add/', '/ipd/new/']

    def test_no_dropdown_offers_nine_hyphens(self):
        import re
        for url in self.PAGES:
            with self.subTest(page=url):
                resp = self.c.get(url, follow=True)
                self.assertEqual(resp.status_code, 200, url)
                options = re.findall(r'<option[^>]*>([^<]*)</option>',
                                     resp.content.decode())
                self.assertNotIn('---------', [o.strip() for o in options],
                                 f'{url} still shows Django\'s raw empty option')

    def test_the_replacement_says_what_to_choose(self):
        html = self.c.get('/medicines/add/', follow=True).content.decode()
        self.assertIn('choose a category', html)

    def test_wording_a_form_chose_for_itself_is_left_alone(self):
        """`VisitForm` deliberately says "All departments" — a filter that
        overwrote that would be worse than the hyphens."""
        from opd.forms import VisitForm
        from accounts.templatetags.form_extras import friendly_empty_labels
        form = friendly_empty_labels(VisitForm(user=self.admin))
        self.assertEqual(form.fields['department'].empty_label,
                         'All departments')


class MoneyCarriesItsSymbolTest(TenantCase):
    """#20 — the patient record and the billing screen disagreed about whether
    an amount needs its currency, on two views of the same money."""

    def setUp(self):
        super().setUp()
        from billing.models import Invoice
        self.p = Patient.objects.create(full_name='Money Patient',
                                        hospital=self.h, gender='M')
        Invoice.objects.create(patient=self.p, hospital=self.h,
                               total=1500, paid=1500,
                               created_by=self.admin)

    def test_the_patient_record_prefixes_its_amounts(self):
        html = self.c.get(f'/patients/{self.p.pk}/', follow=True).content.decode()
        self.assertNotIn('<td>1500.00</td>', html,
                         'an amount on the patient record has no currency')

    def test_a_zero_balance_is_written_like_every_other_amount(self):
        """A fully-paid row showed a bare `0` while every other cell in the
        column carried the symbol, so one column read as two kinds of number."""
        html = self.c.get(f'/billing/patient/{self.p.pk}/',
                          follow=True).content.decode()
        self.assertNotIn('>0</td>', html)


class ExpiryReportOpensWithBatchlessStockTest(TenantCase):
    """The expiry report returned **500** for admin and pharmacist.

    The block that lists stock with no purchase batch read `Medicine.cost_price`,
    which does not exist — cost lives on `StockBatch`. The smoke test opens this
    page and passed, because with no such medicine in the fixture the loop body
    never ran: the crash needed the exact data the feature was written for.

    So the fixture here is the point. Do not simplify it away.
    """

    def _medicine(self, **kw):
        from inventory.models import Medicine
        defaults = dict(name='Loose Syrup', hospital=self.h, price=100,
                        quantity=3, reorder_level=10)
        defaults.update(kw)
        return Medicine.objects.create(**defaults)

    def test_it_opens_when_a_medicine_expires_and_has_no_batch(self):
        self._medicine(expiry_date=date.today() + timedelta(days=18))
        resp = self.c.get('/medicines/expiry/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Loose Syrup', resp.content.decode())

    def test_it_opens_for_already_expired_batchless_stock_too(self):
        self._medicine(name='Old Syrup', expiry_date=date.today() - timedelta(days=5))
        self.assertEqual(self.c.get('/medicines/expiry/').status_code, 200)

    def test_cost_is_reported_as_unknown_never_as_zero(self):
        """There is no cost field to read, and valuing the write-off at retail
        would overstate it. Same rule as the profit report."""
        self._medicine(expiry_date=date.today() + timedelta(days=18))
        html = self.c.get('/medicines/expiry/', follow=True).content.decode()
        self.assertIn('not recorded', html)

    def test_stock_that_is_not_expiring_is_left_out(self):
        self._medicine(name='Fine Syrup', expiry_date=date.today() + timedelta(days=800))
        html = self.c.get('/medicines/expiry/', follow=True).content.decode()
        self.assertNotIn('Fine Syrup', html)
