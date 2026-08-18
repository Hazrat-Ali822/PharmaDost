"""Shrink an uploaded logo before it is stored.

The logo is the one upload in the system that is on the screen all day: it is in
the sidebar of every page, on the sign-in form, on every printed bill and lab
report, and it is what the PWA installs as the home-screen icon. Nothing stopped
a tenant uploading a phone photograph straight off the camera roll, and one did
— the public demo's logo was a 1 MB, 3369x4160 JPEG of a pair of trainers,
fetched on every render of every page.

512px on the long edge is larger than any of the places it is drawn needs (the
biggest is the 512px PWA icon; the sidebar draws it at about 40) and lands under
~60 KB.

Three things here are deliberate:

* **Transparency is preserved.** A logo sits on the dark sidebar *and* on a
  white letterhead, so flattening alpha onto white puts a white block behind it
  in the sidebar — which is the exact defect the default mark had to be
  regenerated to fix (see the note on `static/img/sehatyar-logo.png` in
  CLAUDE.md). A picture with an alpha channel stays PNG; everything else
  becomes JPEG.
* **A file that is already small is returned untouched**, format and all.
  Re-encoding a hand-made 8 KB SVG-exported PNG gains nothing and can only lose.
* **Failure is not fatal** and the handle is always rewound. The settings screen
  reads the same upload again to pick the theme colour out of it, so leaving the
  file at EOF would silently turn "pick the colour from my logo" into "could not
  read a colour from the logo".
"""
import io
import logging
import os

from django.core.files.uploadedfile import InMemoryUploadedFile

logger = logging.getLogger(__name__)

MAX_LOGO_EDGE = 512
JPEG_QUALITY = 82

# Refused at the form with a readable message rather than processed. A logo this
# big is a mistake — somebody has picked the wrong file — and saying so is more
# use than silently storing it.
MAX_LOGO_BYTES = 4 * 1024 * 1024

# Below this, keep whatever was uploaded exactly as it is.
LEAVE_ALONE_BYTES = 100 * 1024


def compress_logo(uploaded):
    """Return a small version of `uploaded`, or the original if it can't be."""
    try:
        from PIL import Image, ImageOps
    except ImportError:                      # Pillow is a dependency; be safe anyway
        return uploaded

    try:
        if uploaded.size <= LEAVE_ALONE_BYTES:
            return uploaded

        uploaded.seek(0)
        img = Image.open(uploaded)
        img = ImageOps.exif_transpose(img)   # a logo shot on a phone is still a photo

        # Keep the alpha channel if there is one: the sidebar is dark and the
        # letterhead is white, and the same file has to sit on both.
        has_alpha = img.mode in ('RGBA', 'LA') or (
            img.mode == 'P' and 'transparency' in img.info)
        img = img.convert('RGBA' if has_alpha else 'RGB')
        img.thumbnail((MAX_LOGO_EDGE, MAX_LOGO_EDGE), Image.LANCZOS)

        buf = io.BytesIO()
        if has_alpha:
            img.save(buf, format='PNG', optimize=True)
            fmt, ext, content_type = 'PNG', '.png', 'image/png'
        else:
            img.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            fmt, ext, content_type = 'JPEG', '.jpg', 'image/jpeg'

        size = buf.tell()
        if size >= uploaded.size:
            # Re-encoding made it bigger (a flat PNG logo usually does). Keep
            # the original rather than storing a worse copy of it.
            uploaded.seek(0)
            return uploaded

        buf.seek(0)
        name = (os.path.splitext(uploaded.name or 'logo')[0] or 'logo') + ext
        logger.debug('logo %s: %s bytes -> %s bytes (%s)',
                     uploaded.name, uploaded.size, size, fmt)
        return InMemoryUploadedFile(buf, 'ImageField', name, content_type, size, None)
    except Exception as exc:                 # noqa: BLE001 — never lose the upload
        logger.warning('could not shrink logo %s: %s',
                       getattr(uploaded, 'name', '?'), exc)
        return uploaded
    finally:
        # The settings view reads this same upload again to pick the theme
        # colour out of it; a handle left at EOF makes that quietly fail.
        try:
            uploaded.seek(0)
        except Exception:                    # noqa: BLE001
            pass
