"""Template helpers for rendering forms the way the rest of the product looks.

`partials/_form.html` can fix labels, errors and help text from the template.
It cannot fix the **empty option** of a dropdown: `ModelChoiceField.empty_label`
is set on the *instance* in `__init__` (default `"---------"`), so neither a
class attribute nor a template can reach it, and Django renders those nine
hyphens as the first choice in every unset dropdown in the app.

Setting it per field in every form's `__init__` would be a hundred edits and the
hundred-and-first form would be wrong again, so it is done once here, at render.
"""
from django import template
from django.forms import ChoiceField
from django.forms.models import ModelChoiceField

register = template.Library()

# Django's default. Only this exact value is replaced — a form that has chosen
# its own wording ("All departments", "Choose an employee…") is left alone.
DJANGO_DEFAULT = '---------'


@register.filter
def friendly_empty_labels(form):
    """Replace Django's `---------` with words, then return the form unchanged.

    A filter with a side effect, which is unusual, but the alternative is a
    hundred `__init__`s that each have to remember. It runs once per form
    render and touches nothing that was deliberately set.
    """
    try:
        fields = form.fields
    except AttributeError:
        return form
    for name, field in fields.items():
        label = (field.label or name.replace('_', ' ')).strip().lower()
        wording = f'— choose a {label} —' if label else '— choose —'

        # A foreign key: the blank option is `empty_label`.
        if isinstance(field, ModelChoiceField):
            if getattr(field, 'empty_label', None) == DJANGO_DEFAULT:
                field.empty_label = wording
            continue

        # A model field with `choices` and `blank=True`: Django prepends
        # `BLANK_CHOICE_DASH`, which is the same nine hyphens arriving by a
        # completely different route. `category` on Medicine is this kind, which
        # is why fixing only ModelChoiceField left the Add Medicine form still
        # showing it.
        if isinstance(field, ChoiceField):
            try:
                choices = list(field.choices)
            except TypeError:
                continue
            if choices and choices[0][0] == '' and choices[0][1] == DJANGO_DEFAULT:
                choices[0] = ('', wording)
                field.choices = choices
    return form
