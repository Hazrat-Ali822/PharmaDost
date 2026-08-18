"""One date format for the whole product: DD/MM/YYYY.

A QA pass counted **five** formats for the same kind of data, two of them
regularly on one screen — `2026-08-15 06:37 PM` on Prescriptions, `18-Aug-2026`
on Appointments, `18 Aug 2026` on the OPD slip, `Aug. 15, 2027` in Inventory,
`17/08/2026` on Messages. On the patient record the Visits row said
"Aug. 18, 2026" and the Bills row directly below it said "2026-08-18".

Most of those were not a decision by anybody. Django localises dates through the
active locale, and the `en` locale renders `N j, Y` — "Aug. 15, 2027" — so every
template that printed a date **without** a `|date:` filter got the American
format for free. Fixing that template by template is 45 edits and the next
template added is wrong again.

`FORMAT_MODULE_PATH` in settings points at this package instead, so the default
is right everywhere and a `|date:` filter is only needed when a screen genuinely
wants something else.

DD/MM/YYYY, not MM/DD/YYYY, and never a bare month name: staff here type and
read `29/01/2002`, and CLAUDE.md already requires typed dates in that shape
(which is why the app avoids `<input type="date">` for them — it renders in the
*browser's* locale, so the same record reads differently at two desks).

`Y-m-d` is still correct in exactly one place — the `value` of an
`<input type="date">`, which the HTML spec fixes as ISO regardless of display.
Do not "tidy" those.
"""

DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'd/m/Y H:i'
SHORT_DATE_FORMAT = 'd/m/Y'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
TIME_FORMAT = 'H:i'
YEAR_MONTH_FORMAT = 'F Y'
MONTH_DAY_FORMAT = 'j F'

# What the server ACCEPTS when a date is typed. Day-first first, so 03/04/2026
# is 3 April — the reading everybody here intends.
DATE_INPUT_FORMATS = [
    '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
    '%Y-%m-%d',            # ISO, for <input type="date"> and any API caller
]
DATETIME_INPUT_FORMATS = [
    '%d/%m/%Y %H:%M', '%d/%m/%Y %H:%M:%S',
    '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%dT%H:%M',      # what <input type="datetime-local"> posts
]
