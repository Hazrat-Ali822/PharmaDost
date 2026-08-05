"""Inline QR code for printed documents.

`qrcode` is an optional dependency (already used by the LAN "Connect a Device"
page). If it isn't installed the tag returns '' and the template simply shows no
QR — a printed bill must never fail to render because a QR could not be drawn.
The QR encodes plain text, so scanning it with any phone camera shows the bill
summary with no internet at all.
"""
import base64
import io

from django import template

register = template.Library()


@register.simple_tag
def qr_data_uri(text, box_size=3):
    """Return a data: URI PNG QR of ``text``, or '' when nothing usable."""
    if not text:
        return ""
    try:
        import qrcode
    except Exception:
        return ""
    try:
        qr = qrcode.QRCode(box_size=box_size, border=1)
        qr.add_data(str(text))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, "PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return ""
