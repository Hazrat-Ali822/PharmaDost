"""Serve uploaded files, because on the deployed host nothing else does.

`pharma_mgmt/urls.py` used `django.conf.urls.static.static()`, which **returns an
empty list when `DEBUG` is False**. DEBUG is off on the hosted site, so `/media/`
404'd there — every tenant's uploaded logo silently fell back to the default
Sehatyar mark via the `onerror` handler on each `<img>`, which is exactly the
shape of failure nobody reports: the page looks fine, it is just showing somebody
else's brand. Confirmed by fetching a real uploaded logo and getting a 404.

WhiteNoise only serves `STATIC_ROOT`, and the Apache config in front of a cPanel
Python app is not something the deployment can rely on, so this goes through
Django. The volumes are a logo, some medicine pictures and staff photos; at that
scale the cost is a rounding error next to being wrong.

**`patient_docs/` is refused here.** A prescription photograph is a medical
record and is served by `patients.views.document_file`, behind the login and the
tenant scope. It shares `MEDIA_ROOT` — one upload tree, one folder to back up —
so the public door has to know not to open it. The check is on the *URL path*
before the file is ever located, and it casefolds, because a case-sensitive
`startswith` is no guard on a case-insensitive filesystem.
"""
from django.conf import settings
from django.http import Http404
from django.views.static import serve as _serve

# URL prefixes under MEDIA_ROOT that must never be public.
PRIVATE_PREFIXES = ('patient_docs/',)

# Uploads are content-addressed enough in practice (a logo changes name when it
# is replaced) and are re-fetched on every page without this.
PUBLIC_MAX_AGE = 60 * 60 * 24 * 7


def serve_media(request, path):
    """Public uploads only. Anonymous is allowed on purpose — the tenant's logo
    is on the sign-in page, before anyone has logged in."""
    if any(path.casefold().startswith(p) for p in PRIVATE_PREFIXES):
        # 404 rather than 403: whether a given patient document exists is itself
        # not this door's business to confirm.
        raise Http404('Not available here.')
    response = _serve(request, path, document_root=settings.MEDIA_ROOT)
    response['Cache-Control'] = f'public, max-age={PUBLIC_MAX_AGE}'
    return response
