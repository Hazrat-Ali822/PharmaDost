import calendar
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone
from saas.utils import TenantManager

class Patient(models.Model):
    GENDER_CHOICES = (
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    )

    # Unique WITHIN a hospital, not globally: every tenant numbers its own
    # patients from 1, so two hospitals both holding SGH-000001 / GUL-000001 is
    # correct. Left blank on create, `patients.services` allocates the next one.
    mrn = models.CharField(max_length=20, blank=True, db_index=True)
    full_name = models.CharField(max_length=255)
    guardian_name = models.CharField(max_length=255, blank=True)
    cnic = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=20, db_index=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    age_years = models.PositiveIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    address = models.TextField(blank=True)
    blood_group = models.CharField(max_length=10, blank=True)
    allergies = models.TextField(blank=True)
    # Panel / Insurance / Sehat Card cover. When set, the patient's bills are
    # attributed to this payer (see billing.services). SET_NULL so removing a
    # panel only unlinks patients, never deletes them.
    panel = models.ForeignKey('panels.Panel', on_delete=models.SET_NULL, null=True, blank=True, related_name='patients')
    panel_member_id = models.CharField(max_length=50, blank=True)   # card / policy no
    # Cap on what the panel will cover for this patient (e.g. Sehat Card annual
    # limit). 0 = unlimited. Enforced at billing: the panel owes at most the
    # remaining coverage, any excess falls to the patient (panels.services).
    panel_coverage_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            UniqueConstraint(fields=['hospital', 'mrn'],
                             condition=~Q(mrn=''),
                             name='uniq_mrn_per_hospital'),
            # A NULL hospital does not participate in the constraint above (SQL
            # treats NULLs as distinct), so a single-site install needs its own.
            UniqueConstraint(fields=['mrn'],
                             condition=Q(hospital__isnull=True) & ~Q(mrn=''),
                             name='uniq_mrn_without_hospital'),
        ]

    @staticmethod
    def age_on(dob, on=None):
        """Completed years between `dob` and `on` (today by default)."""
        if not dob:
            return None
        on = on or timezone.localdate()
        years = on.year - dob.year
        if (on.month, on.day) < (dob.month, dob.day):
            years -= 1
        return max(years, 0)

    @staticmethod
    def _add_months(start, count):
        """`start` shifted by `count` months, clamped to the target month's length
        so 31 Jan + 1 month is 28 Feb rather than rolling into March."""
        year, month = divmod(start.month - 1 + count, 12)
        year, month = start.year + year, month + 1
        return start.replace(year=year, month=month,
                             day=min(start.day, calendar.monthrange(year, month)[1]))

    @staticmethod
    def age_parts_on(dob, on=None):
        """(years, months, days) between `dob` and `on`.

        Counts whole months first, then measures the leftover days from that
        month-anniversary. Subtracting the calendar fields and borrowing a fixed
        number of days does not work — 31 Jan to 1 Mar borrows 28 from February
        and lands on a negative day count.
        """
        if not dob:
            return None
        on = on or timezone.localdate()
        if on <= dob:
            return (0, 0, 0)
        total_months = (on.year - dob.year) * 12 + (on.month - dob.month)
        if on.day < dob.day:
            total_months -= 1
        anniversary = Patient._add_months(dob, total_months)
        years, months = divmod(total_months, 12)
        return (years, months, (on - anniversary).days)

    @property
    def age_parts(self):
        return self.age_parts_on(self.dob)

    @property
    def current_age(self):
        """Age in whole years — for logic and `{% if %}`. Computed live from the
        date of birth when we have one, because `age_years` is only true on the
        day it was entered: a patient registered at 30 otherwise reads 30 five
        years later."""
        return self.age_on(self.dob) if self.dob else self.age_years

    @property
    def guardian_label(self):
        """"s/o" or "d/o" — the guardian relation, matched to the patient.

        Both the reception search results and the printed OPD token slip said
        **s/o** ("son of") for everybody, so a female patient was handed a slip
        with her father's name and the wrong relation on it. Nothing in the
        record says whether an adult woman is d/o or w/o, so d/o is used: it is
        right for a child and for an unmarried woman, and a hospital slip is not
        the place to infer a marital status the patient never gave.
        """
        return {'M': 's/o', 'F': 'd/o'}.get(self.gender, 'c/o')

    @property
    def age_display(self):
        """What to print: '34y 5m 12d', '7m 3d', '4d'.

        Zero parts are dropped, which is the whole point — a six-month-old shown
        as '0 yrs' tells a paediatrician nothing, and a 34-year-old does not need
        their days spelled out unless they happen to be non-zero.
        """
        parts = self.age_parts
        if parts is None:
            # Only a typed age on file, so years is all we can honestly say.
            return f"{self.age_years}y" if self.age_years else ''
        years, months, days = parts
        chunks = [f"{n}{unit}" for n, unit in ((years, 'y'), (months, 'm'), (days, 'd')) if n]
        return ' '.join(chunks) if chunks else 'Newborn'

    def save(self, *args, **kwargs):
        """Allocate an MRN on first save when one wasn't typed in.

        Done here rather than in the form so every entry point — the reception
        screen, `seed_demo`, an import script, a test fixture — produces a
        properly numbered patient instead of a blank MRN.
        """
        if not self.mrn and not self.pk:
            from saas.utils import get_current_hospital
            from .services import next_mrn
            # `saas.signals.auto_assign_hospital` stamps the hospital, but it is a
            # pre_save receiver and so fires INSIDE super().save() — too late to
            # pick the counter. Resolve it here, the same way, or a patient
            # registered through the web would be numbered off the global counter.
            if not self.hospital:
                self.hospital = get_current_hospital()
            self.mrn = next_mrn(self.hospital)

        # A date of birth is fact; a typed age is a snapshot that goes stale. When
        # we have the date, it decides. We deliberately do NOT invent a date of
        # birth from an age here — that would put a precise-looking but made-up
        # date on a medical record. The form offers one the user can see and edit.
        if self.dob:
            self.age_years = self.age_on(self.dob)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.mrn})"


def _document_path(instance, filename):
    """One folder per patient, so a records request is one directory."""
    import os
    ext = (os.path.splitext(filename)[1] or '.jpg').lower()
    return f'patient_docs/{instance.patient_id}/{uuid.uuid4().hex}{ext}'


def _thumb_path(instance, filename):
    return f'patient_docs/{instance.patient_id}/thumbs/{uuid.uuid4().hex}.jpg'


class PatientDocument(models.Model):
    """A photograph of a paper document, attached to the patient's record.

    Most doctors here write on paper and are not going to stop. The system
    already worked around that — reception books the visit, the pharmacist sells
    from the paper prescription at the POS, and the money and stock come out
    right — but the *paper itself* was never kept, so a year later nobody could
    answer "what was she given last time".

    This does one thing: somebody photographs the sheet and it joins the record.
    Nothing is read out of the image. Handwriting OCR on a doctor's prescription
    is 60–70% at best, and a system that reads `Amlodipine` as `Amoxicillin` or
    `5mg` as `50mg` is not a partly-working feature, it is a dangerous one —
    people stop checking exactly because it usually works. So the lab, the
    pharmacy and the ward keep entering what they enter today, and this is the
    evidence sitting beside it.

    **Not offline.** `static/js/offline.js` cannot queue a file input (it drops
    them with a toast and re-attaches once online), so the upload needs a
    connection. The clinical entry it accompanies does not.
    """

    KIND_CHOICES = [
        ('RX', 'Prescription'),
        ('LAB', 'Lab report'),
        ('SCAN', 'Scan / X-ray report'),
        ('DISCHARGE', 'Discharge / referral letter'),
        ('ID', 'ID card / Sehat Card'),
        ('CONSENT', 'Signed consent'),
        ('OTHER', 'Other'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='documents')
    # Which visit it belongs to, when it is known. Optional: a photo brought in
    # off the street belongs to the patient, not to any appointment.
    appointment = models.ForeignKey('opd.Appointment', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='documents')
    image = models.ImageField(upload_to=_document_path)
    # A ~320px copy for the grid. Without it every tile downloads the full
    # picture and the CSS shrinks it: a dozen photos is ~4 MB per visit to the
    # record, on a clinic connection, to show a dozen postage stamps. Nullable
    # because it is an optimisation — `document_file` falls back to the full
    # image, so a row without one still works.
    thumbnail = models.ImageField(upload_to=_thumb_path, blank=True, null=True)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default='RX')
    title = models.CharField(max_length=120, blank=True,
                             help_text='Optional — "Dr. Sara, BP medicines"')
    doc_date = models.DateField(default=timezone.localdate,
                                help_text='The date on the paper, not today')
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='+')
    uploaded_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE,
                                 null=True, blank=True, related_name='patient_documents')

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ('-doc_date', '-uploaded_at')

    def __str__(self):
        return f'{self.get_kind_display()} — {self.patient.full_name} ({self.doc_date})'

    @property
    def label(self):
        return self.title or self.get_kind_display()
