"""Tell patients the things the system already knows but could never say.

Until now there was no way to reach anybody: no mail backend, no SMS. The app
knew tomorrow's appointments, which lab reports were ready and which children
were due a vaccine, and none of it could leave the building.

Run daily from cron (chain it with the existing alert commands — the free tier
allows one scheduled task):

    python manage.py send_reminders

Safe to run twice. Every message carries a `dedupe_key` naming the thing and the
date, and `messaging.services.already_sent` refuses a repeat — on a shared host
you cannot be certain the task ran exactly once, and a patient messaged twice
about the same appointment stops reading the messages.

`already_sent` counts only messages that were actually **SENT**. One that was
SKIPPED (no gateway configured) or FAILED is offered again next run, which is
right — nobody was messaged, so nothing has been used up. It also means that on
an install with no SMS gateway this command lists the same reminders every day,
and **the summary must not call those "sent"**: it reports what left the
building, separately from what could not.

Iterates hospital by hospital and binds the tenant, because these queries run
through `TenantManager` and a command binds no hospital of its own.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from messaging.services import already_sent, notify
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


class Command(BaseCommand):
    help = "Send appointment, lab-result and vaccination reminders."

    def add_arguments(self, parser):
        parser.add_argument('--hospital', dest='slug', default=None,
                            help='Only this hospital (slug). Default: all.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be sent and send nothing.')

    def handle(self, *args, **options):
        if options['slug']:
            hospitals = list(Hospital.objects.filter(slug=options['slug']))
            if not hospitals:
                self.stderr.write(f"No hospital with slug '{options['slug']}'.")
                return
        else:
            # `None` is the hospital-less desktop / LAN install, which has its
            # own patients and wants its own reminders.
            hospitals = list(Hospital.objects.filter(is_active=True)) + [None]

        self.dry = options['dry_run']
        # Kept apart on purpose. Reporting a SKIPPED message as sent is the kind
        # of reassuring-but-false output that stops anyone looking at Settings ->
        # Messages to find out why no patient ever hears from the system.
        self.tally = {'sent': 0, 'skipped': 0, 'failed': 0}
        total = 0
        for hospital in hospitals:
            set_current_hospital(hospital)
            try:
                total += self._appointments(hospital)
                total += self._lab_results(hospital)
                total += self._vaccinations(hospital)
            finally:
                clear_current_hospital()

        if self.dry:
            self.stdout.write(self.style.SUCCESS(f'would send {total} reminder(s).'))
            return

        t = self.tally
        self.stdout.write(self.style.SUCCESS(f"sent {t['sent']} reminder(s)."))
        if t['failed']:
            self.stdout.write(self.style.ERROR(
                f"{t['failed']} failed — see Settings -> Messages for the error."))
        if t['skipped']:
            from messaging.services import email_configured, sms_configured
            missing = []
            if not sms_configured():
                missing.append('SMS gateway (PHARMADOST_SMS_URL)')
            if not email_configured():
                missing.append('email host (DJANGO_EMAIL_HOST)')
            reason = (' — no ' + ' and no '.join(missing) + ' configured'
                      if missing else '')
            self.stdout.write(self.style.WARNING(
                f"{t['skipped']} message(s) could not be sent{reason}. "
                f"Nothing reached a patient; they will be offered again next run."))

    # ------------------------------------------------------------------ helpers

    def _brand(self):
        from user_mgmt.models import SiteSettings
        try:
            return SiteSettings.load().brand_name or 'Sehatyar'
        except Exception:
            return 'Sehatyar'

    def _send(self, patient, subject, body, sms_text, kind, key):
        phone = getattr(patient, 'phone', '') or getattr(patient, 'mobile', '')
        email = getattr(patient, 'email', '') or ''
        if not phone and not email:
            return 0
        if already_sent(f'{key}:sms') or already_sent(f'{key}:email'):
            return 0
        if self.dry:
            self.stdout.write(f"  {kind}: {patient.full_name} ({phone or email})")
            return 1

        rows = notify(email=email, phone=phone, subject=subject, body=body,
                      sms_text=sms_text, kind=kind, dedupe_key=key)
        outcome = self._tally(rows)
        self.stdout.write(f"  {kind}: {patient.full_name} ({phone or email}) — {outcome}")
        return 1

    def _tally(self, rows):
        """Record what actually happened to each channel, and describe it."""
        from messaging.models import MessageLog

        seen = []
        for row in rows:
            if row.status == MessageLog.SENT:
                self.tally['sent'] += 1
                seen.append('sent')
            elif row.status == MessageLog.FAILED:
                self.tally['failed'] += 1
                seen.append('FAILED')
            else:
                self.tally['skipped'] += 1
                seen.append('not sent')
        return ', '.join(seen) or 'no channel'

    # ------------------------------------------------------------------ sources

    def _appointments(self, hospital):
        """Tomorrow's booked appointments."""
        from opd.models import Appointment

        tomorrow = timezone.localdate() + timedelta(days=1)
        brand = self._brand()
        count = 0
        # `Appointment` has no `hospital` column and no TenantManager, so
        # binding the tenant does not scope it — the hospital-less pass at the
        # end of `handle` picked up every hospital's appointments a second time.
        # Scope on the patient, which IS tenant-owned. (Same for TestOrder.)
        appointments = (Appointment.objects
                        .filter(appointment_date=tomorrow, status='BOOKED',
                                patient__hospital=hospital)
                        .select_related('patient', 'doctor'))
        for appt in appointments:
            when = appt.slot_time.strftime('%I:%M %p') if appt.slot_time else 'clinic hours'
            body = (f"Dear {appt.patient.full_name}, this is a reminder of your "
                    f"appointment with {appt.doctor.display_name} on "
                    f"{tomorrow:%d %b %Y} at {when}. — {brand}")
            count += self._send(
                appt.patient, f'Appointment reminder — {brand}', body, body,
                'appointment_reminder', f'appt:{appt.pk}:{tomorrow}')
        return count

    def _lab_results(self, hospital):
        """Orders finished but not yet collected."""
        from lab.models import TestOrder

        brand = self._brand()
        today = timezone.localdate()
        count = 0
        orders = (TestOrder.objects
                  .filter(status__in=['Completed', 'Verified'],
                          order_date__date__gte=today - timedelta(days=7),
                          patient__hospital=hospital)
                  .select_related('patient'))
        for order in orders:
            body = (f"Dear {order.patient.full_name}, your lab report is ready "
                    f"for collection. — {brand}")
            count += self._send(order.patient, f'Lab report ready — {brand}',
                                body, body, 'lab_ready', f'lab:{order.pk}')
        return count

    def _vaccinations(self, hospital):
        """Doses due today or overdue in the last week."""
        from vaccination.models import VaccinationRecord

        brand = self._brand()
        today = timezone.localdate()
        count = 0
        due = (VaccinationRecord.objects
               .filter(next_due_date__range=(today - timedelta(days=7), today),
                       patient__hospital=hospital)
               .select_related('patient', 'vaccine'))
        for record in due:
            body = (f"Dear {record.patient.full_name}, the next dose of "
                    f"{record.vaccine.name} is due on "
                    f"{record.next_due_date:%d %b %Y}. — {brand}")
            count += self._send(
                record.patient, f'Vaccination due — {brand}', body, body,
                'vaccination_due', f'vacc:{record.pk}:{record.next_due_date}')
        return count
