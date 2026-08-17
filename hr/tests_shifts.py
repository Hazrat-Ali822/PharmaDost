"""Shifts are configured per hospital, and a night shift crosses midnight.

    python manage.py test hr.tests_shifts --settings=pharma_mgmt.test_settings
"""
from datetime import date, datetime, time, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import User
from hr.models import Shift
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _future():
    return date.today() + timedelta(days=365)


def _at(h, m=0):
    """An aware datetime today at this local clock time."""
    naive = datetime.combine(timezone.localdate(), time(h, m))
    return timezone.make_aware(naive, timezone.get_current_timezone())


class ShiftClockTest(TestCase):
    """The midnight case. A night shift is 21:00 -> 07:00, and every naive
    implementation of "is this shift on now" reports it as never running."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-shift', expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    def test_a_day_shift_covers_only_its_own_hours(self):
        s = Shift(name='Morning', start_time=time(7, 0), end_time=time(14, 0))
        self.assertFalse(s.crosses_midnight)
        self.assertTrue(s.covers(time(7, 0)))
        self.assertTrue(s.covers(time(13, 59)))
        self.assertFalse(s.covers(time(14, 0)))     # the next shift's first minute
        self.assertFalse(s.covers(time(3, 0)))

    def test_a_night_shift_covers_both_sides_of_midnight(self):
        s = Shift(name='Night', start_time=time(21, 0), end_time=time(7, 0))
        self.assertTrue(s.crosses_midnight)
        self.assertTrue(s.covers(time(21, 0)))
        self.assertTrue(s.covers(time(23, 59)))
        self.assertTrue(s.covers(time(0, 1)))
        self.assertTrue(s.covers(time(6, 59)))
        self.assertFalse(s.covers(time(7, 0)))
        self.assertFalse(s.covers(time(15, 0)))

    def test_hours_are_counted_across_midnight_too(self):
        self.assertEqual(Shift(name='N', start_time=time(21, 0), end_time=time(7, 0)).hours, 10.0)
        self.assertEqual(Shift(name='M', start_time=time(7, 0), end_time=time(14, 0)).hours, 7.0)

    def test_current_finds_the_night_shift_at_two_in_the_morning(self):
        """The whole point: at 02:00 the ward is at its thinnest, and the old
        hardcoded rule was the only thing that got this right by accident."""
        Shift.ensure_defaults(self.h)
        now = Shift.current(self.h, at=_at(2))
        self.assertEqual(now.name, 'Night')

    def test_current_follows_the_hospitals_own_times(self):
        """A hospital that starts night duty at 20:00 must not be told the
        evening shift is still on."""
        Shift.all_objects.create(hospital=self.h, name='Day', start_time=time(8, 0),
                                 end_time=time(20, 0), order=0)
        Shift.all_objects.create(hospital=self.h, name='Night', start_time=time(20, 0),
                                 end_time=time(8, 0), order=1)
        self.assertEqual(Shift.current(self.h, at=_at(20, 30)).name, 'Night')
        self.assertEqual(Shift.current(self.h, at=_at(19, 30)).name, 'Day')


class ShiftPerHospitalTest(TestCase):

    def setUp(self):
        self.a = Hospital.objects.create(name='A', slug='a-shift', expiry_date=_future())
        self.b = Hospital.objects.create(name='B', slug='b-shift', expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    def test_each_hospital_gets_its_own_set(self):
        Shift.ensure_defaults(self.a)
        Shift.ensure_defaults(self.b)
        self.assertEqual(Shift.all_objects.filter(hospital=self.a).count(), 3)
        self.assertEqual(Shift.all_objects.filter(hospital=self.b).count(), 3)

    def test_renaming_one_hospitals_shift_leaves_the_other_alone(self):
        Shift.ensure_defaults(self.a)
        Shift.ensure_defaults(self.b)
        s = Shift.all_objects.get(hospital=self.a, name='Night')
        s.name = 'Raat Duty'
        s.save()
        self.assertTrue(Shift.all_objects.filter(hospital=self.b, name='Night').exists())

    def test_defaults_are_created_once_not_topped_back_up(self):
        """A hospital that runs two shifts must not have a third reappear."""
        Shift.ensure_defaults(self.a)
        Shift.all_objects.filter(hospital=self.a, name='Evening').delete()
        Shift.ensure_defaults(self.a)
        self.assertEqual(Shift.all_objects.filter(hospital=self.a).count(), 2)

    def test_the_hospital_less_install_gets_its_own(self):
        """The desktop / LAN build is one clinic with `hospital = NULL`."""
        Shift.ensure_defaults(None)
        self.assertEqual(Shift.all_objects.filter(hospital=None).count(), 3)


class ShiftEditorTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-ed', expiry_date=_future())
        self.other = Hospital.objects.create(name='O', slug='o-ed', expiry_date=_future())
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(email='n@a.com', password='pw',
                                              role='NURSE', hospital=self.h)
        self.c = Client()
        self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        clear_current_hospital()

    def test_admin_adds_a_fourth_shift(self):
        self.c.post('/hr/shifts/', {'action': 'add', 'name': 'Late Night',
                                    'start_time': '00:00', 'end_time': '06:00'})
        self.assertTrue(Shift.all_objects.filter(hospital=self.h, name='Late Night').exists())

    def test_a_nurse_cannot_edit_shifts(self):
        c = Client(); c.login(email='n@a.com', password='pw')
        self.assertEqual(c.get('/hr/shifts/').status_code, 403)

    def test_the_editor_never_shows_another_hospitals_shifts(self):
        Shift.all_objects.create(hospital=self.other, name='Theirs',
                                 start_time=time(9, 0), end_time=time(17, 0))
        body = self.c.get('/hr/shifts/').content.decode()
        self.assertNotIn('Theirs', body)

    def test_a_used_shift_is_switched_off_rather_than_deleted(self):
        """Rosters PROTECT their shift; deleting it would take the history."""
        from ipd.models import Admission, Bed, NurseShift, Ward
        from opd.models import Doctor
        from patients.models import Patient

        ward = Ward.objects.create(name='W', ward_type='General Male',
                                   daily_rate=100, hospital=self.h)
        Bed.objects.create(bed_number='1', ward=ward, hospital=self.h)
        shift = Shift.for_hospital(self.h).first()
        NurseShift.objects.create(nurse=self.nurse, ward=ward, shift=shift,
                                  hospital=self.h)

        self.c.post(f'/hr/shifts/{shift.pk}/delete/')
        shift.refresh_from_db()
        self.assertFalse(shift.is_active)
        self.assertTrue(Shift.all_objects.filter(pk=shift.pk).exists())

    def test_an_unused_shift_is_deleted(self):
        shift = Shift.all_objects.create(hospital=self.h, name='Spare',
                                         start_time=time(1, 0), end_time=time(2, 0))
        self.c.post(f'/hr/shifts/{shift.pk}/delete/')
        self.assertFalse(Shift.all_objects.filter(pk=shift.pk).exists())


class ShiftFormScopingTest(TestCase):
    """A class-level queryset is evaluated once at import with no tenant bound,
    so the dropdown would list — and accept — another hospital's shift."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-f', expiry_date=_future())
        self.other = Hospital.objects.create(name='O', slug='o-f', expiry_date=_future())
        self.nurse = User.objects.create_user(email='n@a.com', password='pw',
                                              role='NURSE', hospital=self.h)
        self.theirs = Shift.all_objects.create(hospital=self.other, name='Theirs',
                                               start_time=time(9, 0), end_time=time(17, 0))

    def tearDown(self):
        clear_current_hospital()

    def test_the_dropdown_holds_only_this_hospitals_shifts(self):
        from ipd.forms import NursingNoteForm
        form = NursingNoteForm(user=self.nurse)
        self.assertNotIn(self.theirs, list(form.fields['shift'].queryset))

    def test_a_post_naming_another_hospitals_shift_is_rejected(self):
        from ipd.forms import ShiftHandoverForm
        form = ShiftHandoverForm({'date': timezone.localdate(), 'shift': self.theirs.pk,
                                  'situation': 'x'}, user=self.nurse)
        self.assertFalse(form.is_valid())
        self.assertIn('shift', form.errors)
