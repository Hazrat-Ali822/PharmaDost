"""One rule for reading an invoice line back as a department.

Revenue is split across the dashboard tiles and the analytics chart by *parsing
the invoice line's description*, because `InvoiceItem` records no service type —
the create paths write a formatted string and the report screens read it back.
That is fragile, and it drifted the moment there were two readers:

  * the dashboard tested `description == 'OPD Consultation'`, missing the
    'OPD Consultation — Dr. Name' form that reception actually produces;
  * the analytics chart tested `description__icontains='CT'`, which matches the
    'ct' inside "Injection", so a ward injection charge was filed as a CT scan.

So both now call `classify(description)` and there is one place to correct.
Matching is by **prefix**, mirroring how the writers build the string:

    billing.services.create_opd_invoice   'OPD Consultation'
    opd.services.bill_and_notify          'OPD Consultation — Dr. Sara Ahmed'
    lab.services                          'Lab: CBC'
    imaging.services                      '<modality display>: Chest'   e.g. 'X-Ray: Chest'
"""

OPD = 'OPD'
LAB = 'LAB'
IMAGING = 'IMAGING'
OTHER = 'OTHER'


def imaging_prefixes():
    """'Ultrasound:', 'X-Ray:', 'CT Scan:' … built from the model's own choices.

    Derived rather than written out, so adding a modality cannot quietly start
    filing that scan's money under "Other". Imported lazily — `billing` is a
    dependency of `imaging`, not the other way round.
    """
    from imaging.models import ImagingStudy
    return tuple(f'{label}:' for code, label in ImagingStudy.MODALITY_CHOICES
                 if code != 'OTHER')


def classify(description):
    """Return OPD / LAB / IMAGING / OTHER for one invoice line."""
    desc = (description or '').strip()
    if desc.startswith('OPD Consultation'):
        return OPD
    if desc.startswith('Lab:'):
        return LAB
    if desc.startswith(imaging_prefixes()):
        return IMAGING
    return OTHER
