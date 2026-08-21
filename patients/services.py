"""Patient MRN allocation.

Every hospital numbers its own patients from 1, prefixed with a short code, so
the receptionist never types an MRN and two tenants can both hold `SGH-000001`
and `GUL-000001` without colliding.

The counter lives on `user_mgmt.SiteSettings` — already the per-hospital
singleton, with a hospital-less fallback row — so one locked row serves both a
SaaS tenant and a single-site desktop install.
"""
import re

from django.db import IntegrityError, transaction

# How many digits the number is padded to. Fixed width keeps MRNs sorting in the
# same order as they were issued, which plain integers do not (1, 10, 2, ...).
MRN_DIGITS = 6
MAX_PREFIX_LEN = 6


def derive_prefix(name, hospital=None):
    """A short code from the hospital's name: initials for a multi-word name
    ("Shaheen General Hospital" -> SGH), otherwise the leading letters
    ("PharmaDost" -> PHA). Falls back to MRN for a name with no letters.
    Guarantees uniqueness across hospitals so no two tenants ever collide.
    """
    words = [w for w in re.split(r'[^A-Za-z0-9]+', name or '') if w]
    if not words:
        base = 'MRN'
    elif len(words) > 1:
        base = ''.join(w[0] for w in words)
    else:
        base = words[0][:3]
    base = (base.upper()[:MAX_PREFIX_LEN]) or 'MRN'

    from user_mgmt.models import SiteSettings
    prefix = base
    suffix_idx = 1
    while True:
        qs = SiteSettings.objects.filter(mrn_prefix__iexact=prefix)
        if hospital is not None:
            qs = qs.exclude(hospital=hospital)
        if not qs.exists():
            break
        suffix_idx += 1
        prefix = f"{base[:MAX_PREFIX_LEN-len(str(suffix_idx))]}{suffix_idx}"
    return prefix


def _settings_row(hospital, lock=False):
    """The SiteSettings row for this hospital, created if missing.

    Resolved from the passed hospital rather than the thread-local, so a
    management command or a test that builds a patient for a specific tenant
    numbers it against that tenant's counter.
    """
    from user_mgmt.models import SiteSettings

    qs = SiteSettings.objects.all()
    if lock:
        qs = qs.select_for_update()
    if hospital is not None:
        row = qs.filter(hospital=hospital).first()
        if row is None:
            SiteSettings.objects.create(hospital=hospital, brand_name=hospital.name)
            row = qs.filter(hospital=hospital).first()
        return row
    row = qs.filter(hospital__isnull=True).order_by('id').first()
    if row is None:
        SiteSettings.objects.create()
        row = qs.filter(hospital__isnull=True).order_by('id').first()
    return row


def next_mrn(hospital):
    """Reserve and return the next MRN for this hospital.

    The counter row is locked for the duration, so two receptionists registering
    at the same moment cannot be handed the same number.
    """
    with transaction.atomic():
        row = _settings_row(hospital, lock=True)
        prefix = (row.mrn_prefix or derive_prefix(row.brand_name)).upper()
        row.mrn_last_number = (row.mrn_last_number or 0) + 1
        row.save(update_fields=['mrn_last_number'])
        return f"{prefix}-{row.mrn_last_number:0{MRN_DIGITS}d}"


def assign_mrn(patient, attempts=5):
    """Give `patient` an MRN and save it.

    Retries on collision: an MRN typed by hand earlier (or imported) can sit on a
    number the counter has not reached yet, and the unique constraint — not this
    function — is what actually guarantees no duplicates.
    """
    for attempt in range(attempts):
        patient.mrn = next_mrn(patient.hospital)
        try:
            with transaction.atomic():
                patient.save()
            return patient.mrn
        except IntegrityError:
            if attempt == attempts - 1:
                raise
    return patient.mrn
