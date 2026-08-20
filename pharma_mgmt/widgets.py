"""Date and time inputs that actually show the value they were given.

`FORMAT_MODULE_PATH` sets `DATE_INPUT_FORMATS` day-first so that a **typed**
`03/04/2026` is read as 3 April — the reading everybody here intends. Django
renders a widget's value with the *first* entry of that same list, so a plain
`forms.DateInput(attrs={'type': 'date'})` emits ``value="20/08/2026"`` into an
``<input type="date">``. The HTML spec fixes that control's value as ISO
``YYYY-MM-DD`` regardless of how the browser displays it, so the browser
silently discards anything else and shows an empty ``dd/mm/yyyy`` box.

That is not cosmetic. It bit twice over:

* The reception visit screen sets today's date as the initial value, and the box
  came up **blank** — so every booking needed the date typed by hand, and one
  submitted without it was rejected.
* Worse on an *edit* form: the stored date renders as blank, and a field that is
  not required then saves that blank back over a real date.

`pharma_mgmt/formats/en/formats.py` already warns not to "tidy" ISO out of these
values. These two widgets are how that warning is enforced rather than
remembered — use them instead of `forms.DateInput` / `forms.TimeInput` anywhere
the widget is an HTML date or time control.
"""
from django import forms


class DateInput(forms.DateInput):
    """`<input type="date">` whose value is always ISO, as the spec requires."""
    input_type = 'date'

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs=attrs, format=format or '%Y-%m-%d')


class TimeInput(forms.TimeInput):
    """`<input type="time">`, valued `HH:MM`.

    Django's default renders `%H:%M:%S`. Browsers accept it, but they then show
    a seconds box the user has to tab through on every appointment.
    """
    input_type = 'time'

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs=attrs, format=format or '%H:%M')
