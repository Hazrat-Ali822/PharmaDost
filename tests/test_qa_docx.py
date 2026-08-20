"""The nine defects found in the owner's own hand-testing pass (Sehatyar.docx).

Each class here is one item off that list, named for the symptom that was
reported rather than for the function that was wrong — a year from now the
symptom is what somebody will search for.

    python manage.py test tests.test_qa_docx --settings=pharma_mgmt.test_settings
"""
import io
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import User
from inventory.models import Medicine
from opd.models import Department, Doctor, DoctorSchedule
from patients.models import Patient, PatientDocument
from saas.models import Hospital


def _exp():
    return date.today() + timedelta(days=365)


def _hospital(name, slug):
    return Hospital.objects.create(name=name, slug=slug,
                                   expiry_date=date.today() + timedelta(days=365))


def _admin(hospital, email):
    u = User.objects.create_user(email=email, password='pw')
    u.role, u.hospital = 'ADMIN', hospital
    u.save()
    return u


# --------------------------------------------------------------------------- 1
class PakistaniMobileIsElevenDigitsTest(TestCase):
    """`0312-732241235456436` saved and sat in the registry looking real."""

    def _clean(self, value):
        from patients.forms import PatientForm
        form = PatientForm(data={'full_name': 'Ibrahim', 'gender': 'M',
                                 'phone': value})
        form.is_valid()
        return form

    def test_a_mobile_with_too_many_digits_is_refused(self):
        form = self._clean('0312-732241235456436')
        self.assertIn('phone', form.errors)
        self.assertIn('11 digits', ' '.join(form.errors['phone']))

    def test_a_mobile_with_too_few_digits_is_refused(self):
        self.assertIn('phone', self._clean('03123456').errors)

    def test_a_correct_mobile_is_stored_in_one_shape(self):
        form = self._clean('03123456789')
        self.assertNotIn('phone', form.errors)
        self.assertEqual(form.cleaned_data['phone'], '0312-3456789')

    def test_the_same_number_written_the_other_two_ways(self):
        for typed in ('+92 312 3456789', '0092-312-3456789'):
            with self.subTest(typed=typed):
                form = self._clean(typed)
                self.assertNotIn('phone', form.errors)
                self.assertEqual(form.cleaned_data['phone'], '0312-3456789')

    def test_a_landline_is_still_accepted_as_typed(self):
        form = self._clean('091-9220123')
        self.assertNotIn('phone', form.errors)
        self.assertEqual(form.cleaned_data['phone'], '091-9220123')

    def test_a_foreign_number_is_still_accepted(self):
        # A referring consultant abroad. Not a Pakistani mobile, so the 11-digit
        # rule must not touch it.
        form = self._clean('+971526249234')
        self.assertNotIn('phone', form.errors)


# --------------------------------------------------------------------------- 2
class ReceptionSearchesAsYouTypeTest(TestCase):
    """The desk had to press Enter or Find for every lookup."""

    def setUp(self):
        self.h = _hospital('Shaheen Health Care', 'shc')
        self.u = _admin(self.h, 'a@shc.test')
        Patient.objects.create(full_name='Ibrahim', hospital=self.h, gender='M')
        self.client.force_login(self.u)

    def test_the_results_block_is_addressable(self):
        # The live search re-fetches this page and lifts `#find-results` out of
        # the reply. Rename or drop that id and typing silently stops working
        # while the Find button goes on working, which is the hardest kind of
        # break to notice.
        body = self.client.get('/opd/reception/').content.decode()
        self.assertIn('id="find-results"', body)
        self.assertIn('id="find-box"', body)

    def test_a_search_still_answers_on_the_server(self):
        body = self.client.get('/opd/reception/', {'q': 'ibrah'}).content.decode()
        self.assertIn('Ibrahim', body)
        self.assertIn('1 match', body)


# --------------------------------------------------------------------------- 3
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachingAPhotoActuallySavesTest(TestCase):
    """Choose a picture, press Save to record, nothing happens, no message.

    `doc_date` was a required form field living inside the collapsed "More
    details" section, so the rejection was real and invisible.
    """

    def setUp(self):
        self.h = _hospital('Shaheen Health Care', 'shc')
        self.u = _admin(self.h, 'a@shc.test')
        self.p = Patient.objects.create(full_name='Ibrahim', hospital=self.h, gender='M')
        self.client.force_login(self.u)

    def _png(self, name='Abdul Aziz.png'):
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (900, 500), (200, 140, 30)).save(buf, 'PNG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')

    def test_a_photo_saves_without_touching_the_date(self):
        resp = self.client.post(f'/patients/{self.p.pk}/photo/add/',
                                {'image': self._png(), 'kind': 'RX',
                                 'title': '', 'note': '', 'doc_date': ''})
        self.assertEqual(resp.status_code, 302)
        doc = PatientDocument.all_objects.get()
        self.assertEqual(doc.doc_date, timezone.localdate())

    def test_a_second_photo_saves_too(self):
        url = f'/patients/{self.p.pk}/photo/add/'
        for i in (1, 2):
            self.client.post(url, {'image': self._png(f'pic {i}.png'), 'kind': 'RX',
                                   'title': '', 'note': '', 'doc_date': ''})
        self.assertEqual(PatientDocument.all_objects.count(), 2)

    def test_a_rejected_upload_says_so_where_it_can_be_seen(self):
        # No file at all. The error must reach the page, not just the form.
        resp = self.client.post(f'/patients/{self.p.pk}/photo/add/',
                                {'kind': 'RX', 'doc_date': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertIn('errorlist', resp.content.decode())


# --------------------------------------------------------------------------- 4
class ADoctorCanSitEveryDayTest(TestCase):
    """Three timing rows and no way to ask for a fourth."""

    def setUp(self):
        self.h = _hospital('Shaheen Health Care', 'shc')
        self.u = _admin(self.h, 'a@shc.test')
        self.client.force_login(self.u)

    def test_the_page_offers_a_way_to_add_a_row(self):
        body = self.client.get('/opd/doctors/add/').content.decode()
        self.assertIn('id="add-timing"', body)
        self.assertIn('id="schedule-blank"', body)
        self.assertIn('id="schedule-rows"', body)
        self.assertIn('__prefix__', body)          # Django's empty_form

    def test_the_day_dropdown_is_not_nine_hyphens(self):
        body = self.client.get('/opd/doctors/add/').content.decode()
        # Only the SELECT options — `---------` also appears in JS comment rules
        # in the page shell, and matching the whole document catches those.
        self.assertNotIn('>---------<', body)
        self.assertIn('choose a day', body)

    def test_seven_days_save_in_one_submit(self):
        data = {
            'full_name': 'Shariq', 'opd_fee': '100', 'followup_fee': '0',
            'followup_valid_days': '7', 'share_percent': '100',
            'schedules-TOTAL_FORMS': '7',
            'schedules-INITIAL_FORMS': '0',
            'schedules-MIN_NUM_FORMS': '0',
            'schedules-MAX_NUM_FORMS': '1000',
        }
        for i in range(7):
            data[f'schedules-{i}-weekday'] = str(i)
            data[f'schedules-{i}-start_time'] = '09:00'
            data[f'schedules-{i}-end_time'] = '14:00'
        resp = self.client.post('/opd/doctors/add/', data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DoctorSchedule.objects.count(), 7)


# --------------------------------------------------------------------------- 5
class TheTitleIsNotPrintedTwiceTest(TestCase):
    """"Dr.Shariq" typed with no space came out "Dr. Dr.Shariq" everywhere."""

    def test_a_title_with_no_space_after_the_dot_is_stripped(self):
        d = Doctor.objects.create(full_name='Dr.Shariq')
        self.assertEqual(d.full_name, 'Shariq')
        self.assertEqual(d.display_name, 'Dr. Shariq')

    def test_the_usual_spellings_all_work(self):
        for typed, stored in [('Dr. Sara Ahmed', 'Sara Ahmed'),
                              ('Dr Sara Ahmed', 'Sara Ahmed'),
                              ('dr.sara ahmed', 'sara ahmed'),
                              ('Prof.Kamal', 'Kamal'),
                              ('Doctor Imran', 'Imran'),
                              ('Sara Ahmed', 'Sara Ahmed')]:
            with self.subTest(typed=typed):
                self.assertEqual(Doctor(full_name=typed).__class__
                                 .objects.create(full_name=typed).full_name, stored)

    def test_a_name_that_merely_starts_with_those_letters_is_left_alone(self):
        # The reason the old test insisted on a trailing space in the first place.
        for name in ('Drakhshan Bibi', 'Draz Khan', 'Professorial Nonsense'):
            with self.subTest(name=name):
                self.assertEqual(Doctor.objects.create(full_name=name).full_name, name)


# --------------------------------------------------------------------------- 6
class DateBoxesShowTheirValueTest(TestCase):
    """`<input type="date">` was fed `20/08/2026`, which browsers discard."""

    def setUp(self):
        self.h = _hospital('Shaheen Health Care', 'shc')
        self.u = _admin(self.h, 'a@shc.test')
        self.dept = Department.objects.create(name='Medicine', hospital=self.h)
        self.doctor = Doctor.objects.create(full_name='Shariq', hospital=self.h,
                                            department=self.dept, opd_fee=Decimal('100'))
        DoctorSchedule.objects.create(doctor=self.doctor, weekday=date.today().weekday(),
                                      start_time='00:01', end_time='23:59')
        self.client.force_login(self.u)

    def test_the_widget_emits_an_iso_value(self):
        from pharma_mgmt.widgets import DateInput
        from django import forms

        class F(forms.Form):
            d = forms.DateField(widget=DateInput(), initial=date(2026, 8, 20))

        self.assertIn('value="2026-08-20"', str(F()['d']))

    def test_the_visit_screen_comes_up_with_todays_date_filled_in(self):
        body = self.client.get('/opd/reception/visit/').content.decode()
        self.assertIn(f'value="{date.today().isoformat()}"', body)

    def test_booking_with_no_date_says_what_is_missing(self):
        resp = self.client.post('/opd/reception/visit/', {
            'full_name': 'Ibrahim', 'gender': 'M',
            'doctor': self.doctor.pk, 'visit_type': 'OPD',
            'appointment_date': '', 'slot_time': '11:30',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['visit_form'].errors.get('appointment_date'))
        # ...and says it on the page, not only in the form object. The whole
        # complaint was that the screen came back looking untouched.
        self.assertIn('errorlist', resp.content.decode())


# --------------------------------------------------------------------------- 7
class OneHospitalNeverSeesAnothersDoctorsTest(TestCase):
    """The payout CSV and the OPD board both listed the demo tenant's doctors.

    `Doctor` was scoped through its linked user account and most doctors have no
    account, so user-less rows were shown to everybody.
    """

    def setUp(self):
        self.mine = _hospital('Shaheen Health Care', 'shc')
        self.theirs = _hospital('Sehatyar Demo Hospital', 'demo')
        self.me = _admin(self.mine, 'a@shc.test')

        self.my_doctor = Doctor.objects.create(full_name='Shariq', hospital=self.mine,
                                               opd_fee=Decimal('100'))
        # No user account at all — the normal case, and the one that leaked.
        self.their_doctor = Doctor.objects.create(full_name='Imran Khan',
                                                  hospital=self.theirs)
        self.client.force_login(self.me)

    def test_the_payout_page(self):
        body = self.client.get('/opd/payouts/').content.decode()
        self.assertIn('Shariq', body)
        self.assertNotIn('Imran Khan', body)

    def test_the_payout_csv(self):
        resp = self.client.get('/opd/payouts/', {'export': 'csv'})
        body = resp.content.decode('utf-8-sig')
        self.assertIn('Shariq', body)
        self.assertNotIn('Imran Khan', body)

    def test_the_opd_availability_board(self):
        body = self.client.get('/opd/board/').content.decode()
        self.assertNotIn('Imran Khan', body)

    def test_the_booking_dropdown_will_not_even_accept_one(self):
        from opd.forms import VisitForm
        offered = VisitForm(user=self.me).fields['doctor'].queryset
        self.assertIn(self.my_doctor, offered)
        self.assertNotIn(self.their_doctor, offered)

    def test_a_hospital_less_user_does_not_see_every_tenants_doctors(self):
        from opd.scoping import scoped_doctors
        stray = User.objects.create_user(email='nowhere@x.test', password='pw')
        stray.role = 'ADMIN'
        stray.save()
        visible = scoped_doctors(stray)
        self.assertNotIn(self.my_doctor, visible)
        self.assertNotIn(self.their_doctor, visible)

    def test_every_doctor_picker_in_the_product_is_scoped(self):
        # Six other forms carried their own copy of the leaking filter.
        from certificates.forms import DeathCertificateForm
        from consent.forms import ConsentRecordForm
        from diagnosis.forms import PatientDiagnosisForm
        from emergency.forms import EmergencyIntakeForm
        from maternity.forms import AntenatalVisitForm
        from referral.forms import ReferralForm

        for form_class, field in [
            (DeathCertificateForm, 'attending_doctor'),
            (ConsentRecordForm, 'doctor'),
            (PatientDiagnosisForm, 'doctor'),
            (EmergencyIntakeForm, 'attending_doctor'),
            (AntenatalVisitForm, 'seen_by'),
            (ReferralForm, 'referring_doctor'),
        ]:
            with self.subTest(form=form_class.__name__):
                qs = form_class(user=self.me).fields[field].queryset
                self.assertNotIn(self.their_doctor, qs)


# --------------------------------------------------------------------------- 8
class TheSameMedicineTwiceIsFlaggedTest(TestCase):
    """Actifed written twice on one prescription produced no warning at all."""

    def test_the_same_row_twice(self):
        from inventory.safety import screen_medicines
        p = Patient.objects.create(full_name='Ibrahim', gender='M')
        # No generic_name — which is why the salt check skipped it entirely.
        med = Medicine.objects.create(name='Actifed', brand='GSK',
                                      price=Decimal('50'), expiry_date=_exp())
        warns = screen_medicines(p, [med, med])
        self.assertTrue(any('DUPLICATE' in w and 'Actifed' in w for w in warns))

    def test_two_products_of_the_same_salt_are_still_flagged_once(self):
        from inventory.safety import screen_medicines
        p = Patient.objects.create(full_name='Ibrahim', gender='M')
        a = Medicine.objects.create(name='Brufen', generic_name='Ibuprofen',
                                    price=Decimal('10'), expiry_date=_exp())
        b = Medicine.objects.create(name='Ibugesic', generic_name='Ibuprofen',
                                    price=Decimal('10'), expiry_date=_exp())
        warns = [w for w in screen_medicines(p, [a, b]) if 'DUPLICATE' in w]
        self.assertEqual(len(warns), 1)

    def test_a_clean_list_stays_quiet(self):
        from inventory.safety import screen_medicines
        p = Patient.objects.create(full_name='Ibrahim', gender='M')
        a = Medicine.objects.create(name='Panadol', generic_name='Paracetamol',
                                    price=Decimal('5'), expiry_date=_exp())
        b = Medicine.objects.create(name='Brufen', generic_name='Ibuprofen',
                                    price=Decimal('10'), expiry_date=_exp())
        self.assertEqual(screen_medicines(p, [a, b]), [])


# --------------------------------------------------------------------------- 9
class OneHospitalNeverSeesAnothersMedicinesTest(TestCase):
    """`ActiveMedicineManager` was fail-open: no hospital bound meant no filter.

    `saas.utils.TenantManager` grew a "strict" branch for exactly this and these
    two hand-written managers never got it.
    """

    def setUp(self):
        self.mine = _hospital('Shaheen Health Care', 'shc')
        self.theirs = _hospital('Sehatyar Demo Hospital', 'demo')
        self.mine_med = Medicine.objects.create(name='Augmentin', hospital=self.mine,
                                                price=Decimal('100'), expiry_date=_exp())
        self.their_med = Medicine.objects.create(name='Demo Syrup', hospital=self.theirs,
                                                 price=Decimal('50'), expiry_date=_exp())

    def test_a_hospital_less_user_sees_neither(self):
        stray = User.objects.create_user(email='nowhere@x.test', password='pw')
        stray.role = 'PHARMACIST'
        stray.save()
        self.client.force_login(stray)
        body = self.client.get('/medicines/').content.decode()
        self.assertNotIn('Augmentin', body)
        self.assertNotIn('Demo Syrup', body)

    def test_a_hospital_user_sees_only_their_own(self):
        self.client.force_login(_admin(self.mine, 'a@shc.test'))
        body = self.client.get('/medicines/').content.decode()
        self.assertIn('Augmentin', body)
        self.assertNotIn('Demo Syrup', body)

    def test_a_command_still_reaches_every_tenant(self):
        # Outside a request nothing is strict, so `reconcile_stock` and friends
        # must still work across the platform.
        self.assertEqual(Medicine.all_objects.count(), 2)
