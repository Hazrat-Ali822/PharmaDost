"""The attendance machine: the device endpoint, and the punches-to-days rules.

The device half is small. The rules half is where money is at stake — these
rows are what `attendance_summary` counts and what `salary_create` deducts
from — so most of what is asserted here is what the build *refuses* to do:
mark a whole staff absent because the machine was unplugged, overwrite a
correction somebody made by hand, or throw away a punch whose enrolment number
nobody has mapped yet.

    python manage.py test hr.tests_biometric --settings=pharma_mgmt.test_settings
"""
from datetime import date, datetime, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .attendance_build import rebuild_attendance
from .models import (Attendance, BiometricDevice, BiometricPunch, LeaveRequest,
                     StaffProfile, UnknownDeviceContact)

SERIAL = 'CGL8231900021'


def _at(day, hh, mm=0):
    """An aware datetime in the hospital's own timezone."""
    return timezone.make_aware(datetime(day.year, day.month, day.day, hh, mm))


class BiometricTestBase(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(
            name='Bio Hospital', slug='bio-h',
            expiry_date=date.today() + timedelta(days=365))
        self.admin = User.objects.create_user(
            email='admin@bio.com', password='pw', role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(
            email='nurse@bio.com', password='pw', role='NURSE', hospital=self.h)
        for u, bio in ((self.admin, '1'), (self.nurse, '7')):
            StaffProfile.objects.create(user=u, hospital=self.h, biometric_id=bio,
                                        monthly_salary=30000)
        self.device = BiometricDevice.objects.create(
            hospital=self.h, serial=SERIAL, name='Main Gate')
        self.day = date.today() - timedelta(days=3)

    def tearDown(self):
        clear_current_hospital()

    def punch(self, uid, when):
        from .attendance_build import resolve_user
        return BiometricPunch.all_objects.create(
            device=self.device, device_user_id=uid, punched_at=when,
            user=resolve_user(self.h, uid), hospital=self.h)


class DeviceEndpointTest(BiometricTestBase):
    """The machine is not a browser: no session, no cookies, no CSRF token."""

    def test_the_handshake_is_answered_with_configuration(self):
        r = Client().get(f'/iclock/cdata?SN={SERIAL}&options=all')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn(SERIAL, body)
        self.assertIn('Realtime=1', body)       # send each punch, not a nightly batch

    def test_it_is_not_redirected_to_the_login_page(self):
        """A 302 is a reply the machine does not follow — the punches simply
        never arrive and nothing anywhere says so."""
        r = Client().get(f'/iclock/cdata?SN={SERIAL}&options=all')
        self.assertNotIn(r.status_code, (301, 302))

    def test_a_posted_punch_is_stored(self):
        body = f'7\t{self.day:%Y-%m-%d} 08:52:13\t0\t1\t0\t0\n'
        r = Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                          data=body, content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        p = BiometricPunch.all_objects.get()
        self.assertEqual(p.device_user_id, '7')
        self.assertEqual(p.user, self.nurse)    # mapped through biometric_id
        self.assertEqual(timezone.localtime(p.punched_at).hour, 8)

    def test_the_same_punch_twice_is_one_row(self):
        """Terminals resend their whole buffer after a network drop, and several
        do it on a timer regardless."""
        body = f'7\t{self.day:%Y-%m-%d} 08:52:13\t0\t1\t0\t0\n'
        for _ in range(3):
            Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                          data=body, content_type='text/plain')
        self.assertEqual(BiometricPunch.all_objects.count(), 1)

    def test_a_space_separated_body_is_read_too(self):
        """Several firmwares do not use tabs, whatever the protocol says."""
        body = f'7 {self.day:%Y-%m-%d} 08:52:13 0 1\n'
        Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                      data=body, content_type='text/plain')
        self.assertEqual(BiometricPunch.all_objects.count(), 1)

    def test_one_unreadable_line_does_not_cost_the_others(self):
        body = (f'7\t{self.day:%Y-%m-%d} 08:00:00\t0\n'
                'garbage\n'
                f'1\t{self.day:%Y-%m-%d} 09:00:00\t0\n')
        r = Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                          data=body, content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(BiometricPunch.all_objects.count(), 2)

    def test_an_unregistered_serial_is_refused_and_remembered(self):
        """One digit wrong is the commonest setup mistake, and the machine shows
        a tick either way. Refusing silently leaves nobody anything to look at."""
        r = Client().get('/iclock/cdata?SN=NOTMINE123&options=all')
        self.assertEqual(r.status_code, 403)
        stray = UnknownDeviceContact.objects.get()
        self.assertEqual(stray.serial, 'NOTMINE123')

    def test_a_deactivated_device_stops_being_believed(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(is_active=False)
        r = Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                          data=f'7\t{self.day:%Y-%m-%d} 08:00:00\t0\n',
                          content_type='text/plain')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(BiometricPunch.all_objects.count(), 0)

    def test_an_unmapped_enrolment_number_is_kept_not_dropped(self):
        """It is the only evidence somebody was at work."""
        Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                      data=f'99\t{self.day:%Y-%m-%d} 08:00:00\t0\n',
                      content_type='text/plain')
        p = BiometricPunch.all_objects.get()
        self.assertIsNone(p.user)
        self.assertEqual(p.device_user_id, '99')

    def test_other_tables_are_accepted_and_ignored(self):
        """OPERLOG and friends. Refusing them makes some firmwares retry for ever."""
        r = Client().post(f'/iclock/cdata?SN={SERIAL}&table=OPERLOG',
                          data='whatever', content_type='text/plain')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(BiometricPunch.all_objects.count(), 0)

    def test_contact_is_recorded_so_a_silent_machine_is_visible(self):
        Client().get(f'/iclock/cdata?SN={SERIAL}&options=all')
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)
        self.assertFalse(self.device.is_silent)


class AttendanceBuildTest(BiometricTestBase):

    def test_first_and_last_punch_become_the_day(self):
        self.punch('7', _at(self.day, 8, 52))
        self.punch('7', _at(self.day, 14, 10))
        self.punch('7', _at(self.day, 17, 3))

        rebuild_attendance(self.h, self.day, self.day)

        a = Attendance.all_objects.get(user=self.nurse, date=self.day)
        self.assertEqual(a.status, 'PRESENT')
        self.assertEqual(a.check_in.hour, 8)
        self.assertEqual(a.check_out.hour, 17)
        self.assertEqual(a.source, Attendance.SOURCE_DEVICE)

    def test_a_short_day_is_a_half_day(self):
        self.punch('7', _at(self.day, 9, 0))
        self.punch('7', _at(self.day, 11, 30))
        rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(
            Attendance.all_objects.get(user=self.nurse, date=self.day).status, 'HALF')

    def test_forgetting_to_punch_out_is_not_an_absence(self):
        self.punch('7', _at(self.day, 8, 30))
        rebuild_attendance(self.h, self.day, self.day)
        a = Attendance.all_objects.get(user=self.nurse, date=self.day)
        self.assertEqual(a.status, 'PRESENT')
        self.assertIsNone(a.check_out)
        self.assertIn('punch-out', a.notes)

    def test_somebody_who_did_not_come_is_absent(self):
        self.punch('1', _at(self.day, 9, 0))        # the admin came in
        self.punch('1', _at(self.day, 17, 0))
        rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(
            Attendance.all_objects.get(user=self.nurse, date=self.day).status, 'ABSENT')

    def test_a_day_nobody_punched_is_never_marked_absent(self):
        """The one that quietly cuts everybody's pay. A switched-off machine, a
        public holiday and a Sunday are indistinguishable from here."""
        quiet = self.day
        working = self.day + timedelta(days=1)
        self.punch('1', _at(working, 9, 0))

        report = rebuild_attendance(self.h, quiet, working)

        self.assertFalse(Attendance.all_objects.filter(date=quiet).exists())
        self.assertIn(quiet, report['no_data_days'])

    def test_approved_leave_is_leave_not_absence(self):
        LeaveRequest.objects.create(user=self.nurse, start_date=self.day,
                                    end_date=self.day, status='APPROVED',
                                    hospital=self.h)
        self.punch('1', _at(self.day, 9, 0))        # machine was alive
        rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(
            Attendance.all_objects.get(user=self.nurse, date=self.day).status, 'LEAVE')

    def test_a_hand_entered_day_is_never_overwritten(self):
        """The reader misses fingers and people punch for each other. A
        correction a later import silently reverses is worse than no import."""
        Attendance.objects.create(user=self.nurse, date=self.day, status='PRESENT',
                                  hospital=self.h, notes='Was here, reader failed',
                                  source=Attendance.SOURCE_MANUAL)
        self.punch('1', _at(self.day, 9, 0))        # machine alive, nurse has no punch

        report = rebuild_attendance(self.h, self.day, self.day)

        a = Attendance.all_objects.get(user=self.nurse, date=self.day)
        self.assertEqual(a.status, 'PRESENT')
        self.assertEqual(a.notes, 'Was here, reader failed')
        self.assertEqual(report['skipped_manual'], 1)

    def test_rebuilding_twice_gives_the_same_answer(self):
        self.punch('7', _at(self.day, 8, 0))
        self.punch('7', _at(self.day, 17, 0))
        rebuild_attendance(self.h, self.day, self.day)
        rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(Attendance.all_objects.filter(
            user=self.nurse, date=self.day).count(), 1)

    def test_the_future_is_not_an_absence(self):
        later = date.today() + timedelta(days=5)
        self.punch('7', _at(self.day, 8, 0))
        report = rebuild_attendance(self.h, self.day, later)
        self.assertLessEqual(report['end'], date.today())
        self.assertFalse(Attendance.all_objects.filter(date__gt=date.today()).exists())

    def test_unmapped_punches_are_reported_rather_than_counted(self):
        self.punch('99', _at(self.day, 8, 0))
        report = rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(report['unmapped'].get('99'), 1)

    def test_mapping_a_number_later_makes_the_old_punches_count(self):
        """Why unmapped punches are kept: the day is recoverable."""
        self.punch('99', _at(self.day, 8, 0))
        self.punch('99', _at(self.day, 17, 0))
        rebuild_attendance(self.h, self.day, self.day)
        self.assertEqual(
            Attendance.all_objects.get(user=self.nurse, date=self.day).status, 'ABSENT')

        StaffProfile.all_objects.filter(user=self.nurse).update(biometric_id='99')
        from .views_biometric import _relink
        _relink(self.h)
        rebuild_attendance(self.h, self.day, self.day)

        a = Attendance.all_objects.get(user=self.nurse, date=self.day)
        self.assertEqual(a.status, 'PRESENT')
        self.assertEqual(a.check_in.hour, 8)


class BiometricIsolationTest(BiometricTestBase):
    """A device belongs to exactly one hospital, and the serial is the key."""

    def setUp(self):
        super().setUp()
        self.other = Hospital.objects.create(
            name='Other Hospital', slug='other-h',
            expiry_date=date.today() + timedelta(days=365))
        self.other_admin = User.objects.create_user(
            email='admin@other.com', password='pw', role='ADMIN', hospital=self.other)

    def test_punches_are_filed_under_the_devices_hospital(self):
        Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                      data=f'7\t{self.day:%Y-%m-%d} 08:00:00\t0\n',
                      content_type='text/plain')
        self.assertEqual(BiometricPunch.all_objects.get().hospital, self.h)

    def test_another_hospital_cannot_register_the_same_serial(self):
        """The serial is the only credential the protocol has, so claiming one
        already in use would be claiming another hospital's attendance feed."""
        c = Client(); c.login(email='admin@other.com', password='pw')
        c.post('/hr/devices/', {'serial': SERIAL, 'name': 'Mine now'})
        self.assertEqual(BiometricDevice.all_objects.filter(serial=SERIAL).count(), 1)
        self.assertEqual(BiometricDevice.all_objects.get(serial=SERIAL).hospital, self.h)

    def test_another_hospitals_devices_are_not_listed(self):
        c = Client(); c.login(email='admin@other.com', password='pw')
        html = c.get('/hr/devices/').content.decode()
        self.assertNotIn(SERIAL, html)
        self.assertNotIn('Main Gate', html)
        # The add form carries example text as placeholders. Keep the fixtures
        # distinct from them, or this test reports a leak that is not one — it
        # already did once, for both the serial and the name.
        self.assertNotIn('AGL7154900032', (SERIAL, 'Main Gate'))

    def test_a_nurse_cannot_open_the_device_screens(self):
        c = Client(); c.login(email='nurse@bio.com', password='pw')
        for path in ('/hr/devices/', '/hr/devices/enrolment/', '/hr/devices/build/'):
            self.assertIn(c.get(path).status_code, (302, 403), path)


class BuildScreenTest(BiometricTestBase):

    def test_preview_writes_nothing(self):
        """Attendance is what payroll deducts from. A bulk write nobody was
        shown first is not something to offer."""
        self.punch('7', _at(self.day, 8, 0))
        self.punch('7', _at(self.day, 17, 0))
        c = Client(); c.login(email='admin@bio.com', password='pw')

        r = c.post('/hr/devices/build/', {
            'action': 'preview', 'start': str(self.day), 'end': str(self.day)})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(Attendance.all_objects.count(), 0)
        self.assertEqual(r.context['report']['present'], 1)

    def test_apply_writes(self):
        self.punch('7', _at(self.day, 8, 0))
        self.punch('7', _at(self.day, 17, 0))
        c = Client(); c.login(email='admin@bio.com', password='pw')
        c.post('/hr/devices/build/', {
            'action': 'apply', 'start': str(self.day), 'end': str(self.day)})
        self.assertTrue(Attendance.all_objects.filter(
            user=self.nurse, date=self.day, status='PRESENT').exists())

    def test_the_screen_says_what_to_type_into_the_machine(self):
        c = Client(); c.login(email='admin@bio.com', password='pw')
        html = c.get('/hr/devices/').content.decode()
        self.assertIn('testserver', html)       # the server address
        self.assertIn('Cloud Server', html)     # where to find it in the menu


class AddEmployeeTest(BiometricTestBase):
    """Somebody on the payroll need not be somebody with a login.

    Before this the only way onto the attendance sheet was a `User`, which needs
    a unique email — so putting a guard on the machine meant inventing one for
    him.
    """

    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.login(email='admin@bio.com', password='pw')

    def test_an_employee_can_be_added_with_no_login_at_all(self):
        r = self.c.post('/hr/staff/add/', {
            'full_name': 'Karim Bux', 'designation': 'Guard',
            'monthly_salary': '25000', 'biometric_id': '12'})
        self.assertEqual(r.status_code, 302)

        p = StaffProfile.all_objects.get(biometric_id='12')
        self.assertEqual(p.user.get_full_name(), 'Karim Bux')
        self.assertEqual(p.designation, 'Guard')
        self.assertEqual(p.hospital, self.h)

    def test_that_account_cannot_be_signed_in_as(self):
        self.c.post('/hr/staff/add/', {'full_name': 'Karim Bux', 'biometric_id': '12'})
        user = StaffProfile.all_objects.get(biometric_id='12').user
        self.assertFalse(user.has_usable_password())
        # and no access even if it somehow were: [] is the documented
        # "exactly this set, even empty" per-user override
        self.assertEqual(user.custom_features, [])

    def test_a_login_is_created_when_it_is_asked_for(self):
        self.c.post('/hr/staff/add/', {
            'full_name': 'Sara Khan', 'wants_login': 'on', 'role': 'RECEPTIONIST',
            'email': 'sara@bio.com', 'password': 'secret123'})
        user = User.objects.get(email='sara@bio.com')
        self.assertTrue(user.check_password('secret123'))
        self.assertEqual(user.role, 'RECEPTIONIST')
        self.assertEqual(user.hospital, self.h)

    def test_a_login_without_an_email_is_refused_rather_than_half_made(self):
        before = User.objects.count()
        r = self.c.post('/hr/staff/add/', {
            'full_name': 'Sara Khan', 'wants_login': 'on', 'password': 'secret123'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), before)

    def test_two_people_cannot_hold_one_enrolment_number(self):
        """The machine only has one of each, so the second would silently
        collect the first one's days."""
        self.c.post('/hr/staff/add/', {'full_name': 'One', 'biometric_id': '12'})
        r = self.c.post('/hr/staff/add/', {'full_name': 'Two', 'biometric_id': '12'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(StaffProfile.all_objects.filter(biometric_id='12').count(), 1)

    def test_punches_that_arrived_before_the_person_existed_are_picked_up(self):
        """People get enrolled on the machine before anyone gets round to adding
        them here, so their first days arrive unmapped."""
        Client().post(f'/iclock/cdata?SN={SERIAL}&table=ATTLOG',
                      data=(f'12\t{self.day:%Y-%m-%d} 08:00:00\t0\n'
                            f'12\t{self.day:%Y-%m-%d} 17:00:00\t0\n'),
                      content_type='text/plain')
        self.assertEqual(BiometricPunch.all_objects.filter(user__isnull=True).count(), 2)

        self.c.post('/hr/staff/add/', {'full_name': 'Karim Bux', 'biometric_id': '12'})

        self.assertEqual(BiometricPunch.all_objects.filter(user__isnull=True).count(), 0)
        rebuild_attendance(self.h, self.day, self.day)
        user = StaffProfile.all_objects.get(biometric_id='12').user
        self.assertEqual(
            Attendance.all_objects.get(user=user, date=self.day).status, 'PRESENT')

    def test_a_nurse_cannot_add_employees(self):
        c = Client(); c.login(email='nurse@bio.com', password='pw')
        self.assertIn(c.get('/hr/staff/add/').status_code, (302, 403))

    def test_the_new_employee_shows_on_the_staff_list(self):
        self.c.post('/hr/staff/add/', {'full_name': 'Karim Bux', 'designation': 'Guard'})
        html = self.c.get('/hr/').content.decode()
        self.assertIn('Karim Bux', html)

    def test_the_device_page_explains_the_whole_sequence(self):
        """The screen has to answer "how do I add an employee", or the serial
        box on its own is a dead end."""
        html = self.c.get('/hr/devices/').content.decode()
        self.assertIn('User Management', html)      # where to enrol the thumb
        self.assertIn('/hr/staff/add/', html)       # where to add the person
        self.assertIn('/hr/devices/enrolment/', html)   # where to link the two

    def test_each_step_says_where_it_happens(self):
        """Half of them are done standing at the machine and half sitting at a
        screen, and not saying which is the thing that confused the owner."""
        html = self.c.get('/hr/devices/').content.decode()
        self.assertIn('ON THE MACHINE', html)
        self.assertIn('IN SEHATYAR', html)

    def test_the_word_scan_is_explained_rather_than_assumed(self):
        """"Punch" is the protocol's word and means nothing to a clinic admin.
        The screens say "scan", and the page defines it."""
        html = self.c.get('/hr/devices/').content.decode()
        self.assertIn('one touch of the reader', html)
        self.assertNotIn('punch', html.lower())
