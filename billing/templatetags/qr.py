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


@register.simple_tag(takes_context=True)
def qr_url_data_uri(context, path, box_size=3):
    """QR of an absolute URL, drawn here rather than fetched from a QR service.

    The four printed documents that carry a QR — OPD slip, patient bill, lab
    report, imaging report — used to build an `<img src>` pointing at
    api.qrserver.com with the target URL in the query string. Two things were
    wrong with that, and the first is serious: those URLs contain the patient's
    `portal_token`, the unguessable secret that is the *only* thing protecting
    their prescriptions, lab results and bills. Every print handed that secret
    to a third party, permanently, in their access log. The second is that it
    needs the internet, so the clinic LAN build printed a broken image.

    Usage, because a tag argument cannot contain `{% url %}`:

        {% url 'patient_portal_hub' patient.portal_token as portal_path %}
        <img src="{% qr_url_data_uri portal_path %}">
    """
    request = context.get('request')
    if request is None or not path:
        return ""
    return qr_data_uri(request.build_absolute_uri(path), box_size)
