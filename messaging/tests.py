"""Sending must never break the thing that triggered it.

These are called from the middle of a booking, a discharge, a lab result. A
gateway that is down, misconfigured, or simply not bought must never lose the
clinical work — so the rule under test throughout is that a send *records* a
problem and returns, and never raises.

    python manage.py test messaging --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, Client, override_settings
from django.utils import timezone

from accounts.models import User
from messaging.models import MessageLog
from messaging.services import (already_sent, normalise_phone, notify,
                                send_email, send_sms)
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=365)


SMTP = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
            EMAIL_HOST='smtp.example.com', DEFAULT_FROM_EMAIL='hi@example.com')


class PhoneNormalisationTest(TestCase):
    """Reception types the number however it is written on the card."""

    def test_every_way_a_pakistani_mobile_is_written(self):
        for written in ('03001234567', '0300 123 4567', '+92 300 1234567',
                        '00923001234567', '923001234567', '0300-1234567'):
            self.assertEqual(normalise_phone(written), '923001234567', written)

    def test_nothing_in_nothing_out(self):
        for empty in ('', None, 'not a number'):
            self.assertEqual(normalise_phone(empty), '')


class EmailTest(TestCase):

    def tearDown(self):
        clear_current_hospital()

    @override_settings(**SMTP)
    def test_a_sent_email_is_recorded(self):
        log = send_email('a@b.com', 'Hello', 'Body', kind='test')
        self.assertEqual(log.status, MessageLog.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_unconfigured_install_skips_rather_than_fails(self):
        """Most installs have no mail server, and the desktop build has no
        internet. A page of red would train the admin to ignore this screen."""
        with override_settings(EMAIL_HOST=''):
            log = send_email('a@b.com', 'Hello', 'Body')
        self.assertEqual(log.status, MessageLog.SKIPPED)

    @override_settings(**SMTP)
    def test_a_broken_mail_server_does_not_raise(self):
        with mock.patch('django.core.mail.EmailMessage.send',
                        side_effect=OSError('connection refused')):
            log = send_email('a@b.com', 'Hello', 'Body')
        self.assertEqual(log.status, MessageLog.FAILED)
        self.assertIn('connection refused', log.error)

    def test_no_address_is_not_a_failure(self):
        self.assertEqual(send_email('', 'x', 'y').status, MessageLog.SKIPPED)


@override_settings(SMS_URL='https://gw.example.pk/send',
                   SMS_PARAMS='key=k&to={to}&text={text}', SMS_METHOD='GET')
class SmsTest(TestCase):

    def tearDown(self):
        clear_current_hospital()

    def _reply(self, status=200, body=b'OK'):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        return resp

    def test_a_sent_sms_is_recorded_against_the_normalised_number(self):
        with mock.patch('messaging.services.urlrequest.urlopen',
                        return_value=self._reply()):
            log = send_sms('0300 1234567', 'Hi', kind='test')
        self.assertEqual(log.status, MessageLog.SENT)
        self.assertEqual(log.to, '923001234567')

    def test_the_gateway_url_carries_the_number_and_text(self):
        seen = {}

        def capture(req, timeout=None):
            seen['url'] = req.full_url
            return self._reply()

        with mock.patch('messaging.services.urlrequest.urlopen', capture):
            send_sms('03001234567', 'Hello there')
        self.assertIn('to=923001234567', seen['url'])
        self.assertIn('Hello', seen['url'])
        self.assertIn('key=k', seen['url'])

    def test_a_gateway_error_is_recorded_and_does_not_raise(self):
        with mock.patch('messaging.services.urlrequest.urlopen',
                        side_effect=OSError('gateway down')):
            log = send_sms('03001234567', 'Hi')
        self.assertEqual(log.status, MessageLog.FAILED)

    def test_an_http_error_status_is_a_failure_not_a_success(self):
        with mock.patch('messaging.services.urlrequest.urlopen',
                        return_value=self._reply(status=402, body=b'no credit')):
            log = send_sms('03001234567', 'Hi')
        self.assertEqual(log.status, MessageLog.FAILED)
        self.assertIn('no credit', log.error)

    @override_settings(SMS_URL='')
    def test_no_gateway_configured_skips(self):
        self.assertEqual(send_sms('03001234567', 'Hi').status, MessageLog.SKIPPED)


class DedupeTest(TestCase):
    """The cron can run twice. A patient messaged twice stops reading them."""

    def tearDown(self):
        clear_current_hospital()

    @override_settings(**SMTP)
    def test_a_sent_message_is_remembered_by_its_key(self):
        send_email('a@b.com', 's', 'b', kind='k', dedupe_key='appt:1:2026-08-18')
        self.assertTrue(already_sent('appt:1:2026-08-18'))
        self.assertFalse(already_sent('appt:2:2026-08-18'))

    def test_a_failure_is_not_remembered_as_sent(self):
        """Otherwise one gateway hiccup silently cancels that reminder for good."""
        with override_settings(EMAIL_HOST='smtp.example.com',
                               DEFAULT_FROM_EMAIL='hi@example.com'):
            with mock.patch('django.core.mail.EmailMessage.send',
                            side_effect=OSError('down')):
                send_email('a@b.com', 's', 'b', dedupe_key='appt:9')
        self.assertFalse(already_sent('appt:9'))


class MessageLogIsolationTest(TestCase):
    """The log holds patient phone numbers and message bodies."""

    def setUp(self):
        self.a = Hospital.objects.create(name='A', slug='a-msg', expiry_date=_future())
        self.b = Hospital.objects.create(name='B', slug='b-msg', expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    @override_settings(**SMTP)
    def test_one_tenants_messages_are_invisible_to_another(self):
        set_current_hospital(self.b)
        try:
            send_email('theirs@b.com', 'Their subject', 'body')
        finally:
            clear_current_hospital()

        set_current_hospital(self.a)
        try:
            self.assertEqual(MessageLog.objects.count(), 0)
        finally:
            clear_current_hospital()
        self.assertEqual(MessageLog.all_objects.count(), 1)


class ReminderCommandTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-rem', expiry_date=_future())
        self.admin = User.objects.create_user(email='a@rem.com', password='pw',
                                              role='ADMIN', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _appointment_tomorrow(self):
        from opd.models import Appointment, Doctor
        docuser = User.objects.create_user(email='d@rem.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        doctor = Doctor.objects.create(user=docuser, full_name='Sara Ahmed',
                                       opd_fee=Decimal('500'))
        patient = Patient.objects.create(full_name='Ali Khan', gender='M',
                                         phone='03001234567', hospital=self.h)
        return Appointment.objects.create(
            patient=patient, doctor=doctor,
            appointment_date=timezone.localdate() + timedelta(days=1))

    @override_settings(SMS_URL='https://gw.example.pk/send')
    def test_tomorrows_appointment_gets_one_reminder(self):
        from django.core.management import call_command

        set_current_hospital(self.h)
        try:
            self._appointment_tomorrow()
        finally:
            clear_current_hospital()

        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = b'OK'
        resp.__enter__.return_value = resp
        with mock.patch('messaging.services.urlrequest.urlopen', return_value=resp):
            call_command('send_reminders', verbosity=0)
            sent = MessageLog.all_objects.filter(kind='appointment_reminder',
                                                 status=MessageLog.SENT).count()
            self.assertEqual(sent, 1)

            # Running the cron again must not message the patient a second time.
            call_command('send_reminders', verbosity=0)
        self.assertEqual(
            MessageLog.all_objects.filter(kind='appointment_reminder',
                                          status=MessageLog.SENT).count(), 1)

    def test_a_dry_run_sends_nothing(self):
        from django.core.management import call_command

        set_current_hospital(self.h)
        try:
            self._appointment_tomorrow()
        finally:
            clear_current_hospital()
        call_command('send_reminders', '--dry-run', verbosity=0)
        self.assertEqual(MessageLog.all_objects.count(), 0)

    def test_the_reminder_names_the_doctor_with_one_title(self):
        from django.core.management import call_command

        set_current_hospital(self.h)
        try:
            self._appointment_tomorrow()
        finally:
            clear_current_hospital()
        with override_settings(SMS_URL='https://gw.example.pk/send'):
            resp = mock.MagicMock()
            resp.status = 200
            resp.read.return_value = b'OK'
            resp.__enter__.return_value = resp
            with mock.patch('messaging.services.urlrequest.urlopen', return_value=resp):
                call_command('send_reminders', verbosity=0)
        body = MessageLog.all_objects.get(kind='appointment_reminder').body
        self.assertIn('Dr. Sara Ahmed', body)
        self.assertNotIn('Dr. Dr.', body)


class MessageLogScreenTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-scr', expiry_date=_future())
        self.admin = User.objects.create_user(email='a@scr.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(email='n@scr.com', password='pw',
                                              role='NURSE', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def test_an_admin_can_read_it(self):
        c = Client()
        c.force_login(self.admin)
        self.assertEqual(c.get('/messages/').status_code, 200)

    def test_a_nurse_cannot(self):
        """It carries every patient's phone number and message text."""
        c = Client()
        c.force_login(self.nurse)
        self.assertEqual(c.get('/messages/').status_code, 403)


class ReminderReportingTest(TestCase):
    """What the command *says* happened must match what happened.

    On the live host, with no SMS gateway configured, the command printed
    "sent 1 reminder(s)." every night. Nothing had reached anybody — the message
    was recorded SKIPPED — and the line read as confirmation that the patient had
    been told. That is the reassuring-but-false output this project keeps having
    to dig out, and it is worse here than a plain failure: it is the only signal
    anyone has that the reminders are not working.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-rep', expiry_date=_future())
        set_current_hospital(self.h)
        from opd.models import Appointment, Doctor
        doctor = Doctor.objects.create(full_name='Sara Ahmed', opd_fee=Decimal('500'))
        patient = Patient.objects.create(full_name='Ali Khan', gender='M',
                                         phone='03001234567', hospital=self.h)
        Appointment.objects.create(patient=patient, doctor=doctor,
                                   appointment_date=timezone.localdate() + timedelta(days=1))

    def tearDown(self):
        clear_current_hospital()

    def _run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command('send_reminders', hospital='h-rep', stdout=out)
        return out.getvalue()

    @override_settings(SMS_URL='', EMAIL_HOST='')
    def test_with_no_gateway_it_does_not_claim_to_have_sent_anything(self):
        output = self._run()
        self.assertIn('sent 0 reminder(s)', output)
        self.assertIn('could not be sent', output)
        self.assertIn('SMS gateway', output, 'it should name what is missing')

    @override_settings(SMS_URL='https://gw.example.pk/send')
    def test_with_a_gateway_a_real_send_is_counted(self):
        with mock.patch('messaging.services.urlrequest.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            output = self._run()
        self.assertIn('sent 1 reminder(s)', output)
        self.assertNotIn('could not be sent', output)

    @override_settings(SMS_URL='https://gw.example.pk/send')
    def test_a_gateway_error_is_reported_as_failed_not_sent(self):
        with mock.patch('messaging.services.urlrequest.urlopen',
                        side_effect=OSError('gateway down')):
            output = self._run()
        self.assertIn('sent 0 reminder(s)', output)
        self.assertIn('failed', output)

    @override_settings(SMS_URL='', EMAIL_HOST='')
    def test_an_unsent_reminder_is_offered_again_next_run(self):
        """Nothing was used up, so the dedupe key must not consume it."""
        self._run()
        self.assertIn('sent 0 reminder(s)', self._run())
        self.assertIn('appointment_reminder', self._run())
