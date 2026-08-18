"""One patient search, used by every screen that looks a patient up.

There were two implementations — `patients.views.patient_list` and
`opd.views.reception_desk` — and they disagreed about what counts as a match, so
the same query found the patient at one desk and not at the other. Both had the
same two holes:

* **A phone number typed without dashes found nothing.** Numbers are stored
  however they were first entered (`0311-1111111` in one row, `03001234567` in
  the next), so a plain `phone__icontains` only matches people whose number
  happens to have been typed in the same shape as the search. Reception reads
  the number off the patient's own phone screen; they are not going to insert
  dashes to match a convention they cannot see. The CNIC has exactly the same
  problem and is always stored dashed.
* **A first name plus a surname found nothing.** `full_name__icontains` is a
  single substring, so "ayesha qadir" does not match "Ayesha Bibi Qadir" — and
  in this market almost every registered name carries a middle name or a
  father's name. Searching for the two parts you actually remember is the normal
  way to look somebody up, and it returned "No patients found".

So the rule here is: **digits match digits, and every word must appear
somewhere in the name.** Words are ANDed, not ORed — "ali khan" must not return
every Ali and every Khan in the register.
"""
from django.db.models import Q, Value
from django.db.models.functions import Replace

# Longer than any MRN or CNIC; stops one pasted paragraph becoming 300 ANDed
# LIKEs against the whole registry.
MAX_TERMS = 6


def _digits(text):
    return ''.join(ch for ch in text if ch.isdigit())


def annotate_for_search(qs):
    """Add the punctuation-stripped copies the digit match compares against.

    Must be applied before `search_filter`, and is separate from it so a caller
    that already annotated (or that wants to order by something first) can
    control when it happens.
    """
    return qs.annotate(
        cnic_digits=Replace(Replace('cnic', Value('-'), Value('')),
                            Value(' '), Value('')),
        phone_digits=Replace(Replace('phone', Value('-'), Value('')),
                             Value(' '), Value('')),
    )


def search_filter(query):
    """A `Q` matching `query` against MRN, name, phone and CNIC.

    Returns None for a blank query so the caller can skip filtering entirely
    rather than build a no-op `Q`.
    """
    q = (query or '').strip()
    if not q:
        return None

    lookup = Q(mrn__icontains=q)

    # Every word has to appear in the name, in any order: "qadir ayesha" finds
    # "Ayesha Bibi Qadir" too, which is how people actually half-remember a name.
    words = [w for w in q.split() if w][:MAX_TERMS]
    if words:
        name = Q()
        for word in words:
            name &= Q(full_name__icontains=word)
        lookup |= name

    digits = _digits(q)
    if digits:
        # Compare digits to digits. Anything else makes finding a patient depend
        # on whoever registered them having typed the separators the same way.
        lookup |= Q(phone_digits__contains=digits) | Q(cnic_digits__contains=digits)
    else:
        lookup |= Q(phone__icontains=q) | Q(cnic__icontains=q)

    return lookup


def apply_search(qs, query):
    """Annotate + filter in one call — what most callers want."""
    lookup = search_filter(query)
    if lookup is None:
        return qs
    return annotate_for_search(qs).filter(lookup)
