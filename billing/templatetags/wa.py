"""Template helper for the free WhatsApp share (wa.me link — no gateway)."""
from django import template

register = template.Library()


@register.filter
def wa_number(phone):
    """Normalise a Pakistani number to the international digits wa.me expects.

    03001234567 -> 923001234567, +92 300… / 0092… / 3001234567 all handled.
    Returns '' when there is nothing usable, so the caller can hide the button.
    """
    if not phone:
        return ""
    digits = "".join(c for c in str(phone) if c.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("92"):
        return digits
    if digits.startswith("0"):
        return "92" + digits[1:]
    if len(digits) == 10 and digits.startswith("3"):
        return "92" + digits
    return digits
