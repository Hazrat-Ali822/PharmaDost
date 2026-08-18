"""How many units to dispense for a prescribed line.

`PrescriptionItem.dosage` is free text, because that is how doctors write and no
dropdown survives contact with a real OPD. The POS still has to turn it into a
number so the pharmacist is not retyping every line of every prescription.

It used to try exactly one shape — `1+0+1` — and fall back to **the number of
days** for anything else. Almost nothing is written as `1+0+1`, so in practice
every line came through as its duration: "1 tab TDS x 5 days" (15 tablets)
loaded as 5, "1 tab BD x 7 days" (14) loaded as 7. Not merely wrong but
*plausibly* wrong — a small number in a quantity box that nobody re-checks —
and short-dispensing a course of antibiotics is a clinical error, not a
counting one.

Two shapes are understood now:

* **Slotted** — `1+0+1`, `1-0-1`, `1/2+0+1/2`. The sum is the daily amount.
* **Amount + frequency** — `1 tab TDS`, `2 tsp BD`, `5ml QID`, `TDS`. The
  leading number (default 1) times the frequency's doses per day.

Anything it cannot read returns None, and the caller leaves the quantity for the
pharmacist rather than inventing one. `PRN` / `SOS` is deliberately in that
group: "as needed" has no quantity, and guessing one is how a patient goes home
with thirty of something they were meant to take twice.
"""
import math
import re

# Doses per day. Keys are matched as whole words, case-insensitively.
FREQUENCIES = {
    'od': 1, 'qd': 1, 'daily': 1, 'once': 1, 'hs': 1, 'nocte': 1, 'om': 1, 'on': 1,
    'bd': 2, 'bid': 2, 'twice': 2,
    'tds': 3, 'tid': 3, 'thrice': 3,
    'qid': 4, 'qds': 4,
    '5x': 5,
}

# "as needed" — a real instruction with no computable quantity.
AS_NEEDED = {'prn', 'sos', 'stat'}

# `q6h`, `q 8 hourly`, `6 hourly`
_HOURLY = re.compile(r'\bq?\s*(\d{1,2})\s*(?:h|hr|hrs|hour|hourly)\b', re.I)
_LEADING_AMOUNT = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)|^\s*(\d+(?:\.\d+)?)')


def _to_float(token):
    token = token.strip()
    if not token:
        return None
    if '/' in token:                      # half a tablet, written 1/2
        a, _, b = token.partition('/')
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(token)
    except ValueError:
        return None


def doses_per_day(dosage):
    """Units per day from a free-text dosage, or None if it cannot be read."""
    text = (dosage or '').strip()
    if not text:
        return None

    words = re.findall(r'[a-z]+', text.lower())
    if any(w in AS_NEEDED for w in words):
        return None

    # --- slotted: 1+0+1 / 1-0-1 / 1/2+0+1/2 -------------------------------
    # Only when the separators are actually separating numbers, so a plain
    # "1-2 tablets" does not get read as two slots.
    slots = re.split(r'[+\-]', text)
    if len(slots) >= 2:
        values = [_to_float(s) for s in slots]
        if all(v is not None for v in values):
            total = sum(values)
            if total > 0:
                return total

    # --- amount + frequency: "1 tab TDS", "2 tsp BD", "TDS" ---------------
    per_day = None
    for word in words:
        if word in FREQUENCIES:
            per_day = FREQUENCIES[word]
            break
    if per_day is None:
        hourly = _HOURLY.search(text)
        if hourly:
            hours = int(hourly.group(1))
            if 1 <= hours <= 24:
                per_day = 24 / hours
    if per_day is None:
        return None

    amount = 1.0
    match = _LEADING_AMOUNT.match(text)
    if match:
        if match.group(1) and match.group(2):
            amount = _to_float(f'{match.group(1)}/{match.group(2)}') or 1.0
        elif match.group(3):
            amount = _to_float(match.group(3)) or 1.0
    return amount * per_day


def dispense_quantity(dosage, duration_days):
    """Units to put in the cart, or None to leave it to the pharmacist.

    Rounded **up**: half a tablet a day for 5 days is 3 tablets handed over, not
    2.5, and never fewer than the course needs.
    """
    per_day = doses_per_day(dosage)
    if per_day is None or per_day <= 0:
        return None
    days = duration_days or 1
    return max(1, int(math.ceil(per_day * days)))
