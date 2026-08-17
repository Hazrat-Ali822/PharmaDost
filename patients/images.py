"""Shrink an uploaded photograph before it is stored.

A phone camera writes 3–8 MB per picture. A busy front desk photographing every
prescription puts a gigabyte a month onto a small shared host, and on the desktop
build every one of those megabytes is copied into the launch-time backup zip and
then uploaded to the cloud backup — so the cost is paid three times.

A prescription sheet does not need 12 megapixels to be read. 1600px on the long
edge at JPEG quality 78 keeps handwriting legible and lands around 200–400 KB.

Two details matter more than the compression:

* **EXIF rotation.** Phones record orientation in metadata rather than rotating
  the pixels, and `<img>` in a page does not always honour it. Without
  `exif_transpose` a portrait photo of a prescription displays on its side,
  which is exactly as useless as not having it.
* **Failure is not fatal.** If Pillow cannot process the file (an odd format, a
  truncated upload), the original is stored unchanged. Losing the record to save
  disk space would be the wrong trade.
"""
import io
import logging

from django.core.files.uploadedfile import InMemoryUploadedFile

logger = logging.getLogger(__name__)

MAX_EDGE = 1600
JPEG_QUALITY = 78
# Anything larger than this is refused at the form rather than processed — a
# 60 MB upload on a shared host is a mistake or an attack, not a prescription.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def compress(uploaded):
    """Return a smaller version of `uploaded`, or the original if it can't be."""
    try:
        from PIL import Image, ImageOps
    except ImportError:                     # Pillow is a dependency; be safe anyway
        return uploaded

    try:
        uploaded.seek(0)
        img = Image.open(uploaded)
        img = ImageOps.exif_transpose(img)          # honour the phone's rotation
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
        size = buf.tell()
        if size >= uploaded.size:
            # Already small, or re-encoding made it bigger. Keep the original.
            uploaded.seek(0)
            return uploaded
        buf.seek(0)
        name = _jpeg_name(uploaded.name)
        return InMemoryUploadedFile(buf, 'ImageField', name, 'image/jpeg', size, None)
    except Exception as exc:                # noqa: BLE001 — never lose the record
        logger.warning('could not compress %s: %s', getattr(uploaded, 'name', '?'), exc)
        try:
            uploaded.seek(0)
        except Exception:                   # noqa: BLE001
            pass
        return uploaded


def _jpeg_name(name):
    import os
    return (os.path.splitext(name or 'photo')[0] or 'photo') + '.jpg'
