"""Progressive Web App plumbing: a per-tenant manifest, icon, service worker and
offline page, so the site installs as an app carrying each hospital's own name,
logo and colour — "Shaheen Health Care", not "Sehatyar".

Everything here reads `SiteSettings.load()`, which resolves the current tenant
from the logged-in user, so the installed app is branded per hospital. The
requests carry the session cookie (same-origin), so tenancy is in scope.

Scope: the service worker handles *reading* offline — it caches the shell and
every data-entry screen, so the app opens and its forms can be filled in with no
connection. Offline *writing* is a separate mechanism, the outbox in
`offline_sync/` (see CLAUDE.md), which queues the submitted entry and replays it
to the server on reconnect. Server-authoritative numbers (MRN, token, sale #) are
still assigned by the server at replay time, never on the device.
"""
import base64
import hashlib
import io
import json
import os

from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control

from .models import SiteSettings


def _branding():
    return SiteSettings.load()


def _theme(branding):
    return branding.primary_color or "#4f46e5"


def manifest(request):
    """The web app manifest — what a browser reads to install the site as an app."""
    b = _branding()
    name = b.brand_name or "Sehatyar"
    data = {
        "name": name,
        "short_name": (name[:12] or "Sehatyar").strip(),
        "description": b.brand_tagline or "Hospital & Pharmacy Management",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#ffffff",
        "theme_color": _theme(b),
        "icons": [
            {"src": reverse("pwa_icon", args=[192]), "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": reverse("pwa_icon", args=[512]), "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


@cache_control(max_age=3600)
def icon(request, size):
    """The app icon shown on the home screen / taskbar.

    Uses the tenant's uploaded logo when there is one; otherwise renders the
    brand letter on the theme colour, so an install always has a real icon rather
    than a broken square.
    """
    from PIL import Image, ImageDraw, ImageFont

    size = 512 if int(size) >= 512 else 192
    b = _branding()
    img = None

    if b.logo_image:
        try:
            src = Image.open(b.logo_image.path).convert("RGBA")
            canvas = Image.new("RGBA", (size, size), _theme(b))
            src.thumbnail((size, size), Image.LANCZOS)
            canvas.paste(src, ((size - src.width) // 2, (size - src.height) // 2), src)
            img = canvas.convert("RGB")
        except Exception:
            img = None

    if img is None:
        img = Image.new("RGB", (size, size), _theme(b))
        draw = ImageDraw.Draw(img)
        letter = (b.logo_text or (b.brand_name or "S")[0] or "S")[:2].upper()
        try:
            font = ImageFont.truetype("arialbd.ttf", int(size * 0.52))
        except Exception:
            font = ImageFont.load_default(size=int(size * 0.52))
        box = draw.textbbox((0, 0), letter, font=font)
        draw.text(((size - (box[2] - box[0])) / 2 - box[0],
                   (size - (box[3] - box[1])) / 2 - box[1]),
                  letter, font=font, fill="#ffffff")

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")


# Every screen a shift might need with no connection: each data-entry form (a
# form that will not open cannot be filled in offline), the list it is reached
# from, and the outbox. Held as url *names* and reversed at request time, so a
# renamed route breaks loudly at the next deploy instead of quietly caching
# nothing.
SHELL_URL_NAMES = [
    'dashboard', 'pwa_offline', 'offline_queue', 'offline_slip',
    'patient_list', 'patient_add',
    'sale_list', 'sale_create',
    'appointment_list', 'appointment_add', 'visit_create',
    'department_list', 'doctor_list', 'doctor_add', 'payout_list',
    'prescription_list', 'prescription_presets',
    'lab:order_list', 'lab:order_create', 'lab:test_catalog',
    'imaging:study_list', 'imaging:study_create', 'imaging:scan_catalog',
    'medicine_list', 'medicine_add', 'adjustment_list', 'adjustment_create',
    'preturn_create',
    'supplier_list', 'supplier_add',
    'customer_list', 'customer_add',
    'ipd:admission_list', 'ipd:admission_create', 'ipd:ward_bed_list',
    'ipd:ward_create', 'ipd:bed_create',
    'ot:surgery_list', 'ot:surgery_create', 'ot:procedure_list',
    'invoice_list', 'expense_list', 'expense_create', 'cash_closing_new',
]

_SHELL_STATIC = ['/static/css/app.css', '/static/js/offline.js']


def _shell_urls():
    from django.urls import NoReverseMatch

    urls = list(_SHELL_STATIC)
    for name in SHELL_URL_NAMES:
        try:
            urls.append(reverse(name))
        except NoReverseMatch:
            continue      # route removed — nothing to pre-cache, not an error
    return urls


def service_worker(request):
    """Served at /sw.js (root) so its scope covers the whole site — a service
    worker can only control pages at or below its own URL.

    The cache name carries the **signed-in user**, not just the tenant. The
    worker caches whole rendered pages, and those pages hold patient names, bills
    and staff lists; a shared device where a doctor signs out and a receptionist
    signs in would otherwise serve the doctor's cached pages to whoever is there
    now. A different user means a different cache name, and `activate` deletes
    every cache that is not the current one — so signing out empties the previous
    user's pages instead of leaving them on the device.
    """
    b = _branding()
    who = request.user.pk if request.user.is_authenticated else "anon"
    shell = _shell_urls()
    version = hashlib.md5(
        f"{b.pk}:{b.updated_at}:{who}:{len(shell)}".encode()).hexdigest()[:8]
    body = (_SERVICE_WORKER
            .replace("__VERSION__", version)
            .replace("__SHELL__", json.dumps(shell)))
    resp = HttpResponse(body, content_type="application/javascript")
    # The SW file itself must never be cached, or updates never reach clients.
    resp["Cache-Control"] = "no-cache"
    return resp


def offline(request):
    """Shown by the service worker when a page is requested with no connection."""
    return HttpResponse(_OFFLINE_PAGE.replace("__THEME__", _theme(_branding()))
                        .replace("__NAME__", _branding().brand_name or "Sehatyar"),
                        content_type="text/html")


from accounts.decorators import feature_required  # noqa: E402


def get_app(request):
    """The 'Get the App' page — an Install button plus per-platform instructions."""
    from django.shortcuts import render
    return render(request, "pwa/get_app.html", _lan_context(request))


def _lan_context(request):
    """What to tell staff about joining this machine over the wifi.

    Only the desktop build sets `PHARMADOST_LAN_URL` (see `desktop/launcher.py`), so
    on the hosted site this is all empty and the page shows nothing about a LAN.
    """
    url = os.environ.get("PHARMADOST_LAN_URL", "")
    ips = [i for i in os.environ.get("PHARMADOST_LAN_IPS", "").split(",") if i]
    return {
        "lan_url": url,
        "lan_extra_urls": [url.replace(ips[0], ip) for ip in ips[1:]] if ips else [],
        "lan_qr": _qr_data_uri(url) if url else "",
    }


def _qr_data_uri(text):
    """A QR of the LAN address, inlined as a data: URI.

    Typing `http://192.168.1.7:8000` into a phone is where clinic staff give up, so
    the QR matters more than it looks. `qrcode` is optional — without it the page
    still shows the address in large type, which is why nothing here raises.
    """
    try:
        import qrcode
    except Exception:
        return ""
    try:
        img = qrcode.make(text)
        buffer = io.BytesIO()
        img.save(buffer, "PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return ""


def lan_connect(request):
    """A big, printable card with the address and QR — stick it on the wall."""
    from django.shortcuts import render
    return render(request, "pwa/lan_connect.html", _lan_context(request))


# --- The service worker itself (network-first for pages, cache-first for assets) ---
_SERVICE_WORKER = r"""
const CACHE = 'pharmadost-__VERSION__';
// Built from Django's own url names (see SHELL_URL_NAMES) — a hand-written path
// that no longer resolves fails silently and the screen is simply not there when
// the connection drops, which is how `/opd/visit/` and `/lab/order/new/` sat in
// this list caching nothing. A URL the user's role cannot reach fails its
// `cache.add` and is skipped, which is fine.
const SHELL = __SHELL__;

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => {
      // Pre-cache shell assets; ignore individual failures so SW installs reliably.
      return Promise.all(
        SHELL.map((url) => c.add(url).catch((err) => console.log('SW shell skip:', url)))
      );
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  // Drop old versions so a redeploy actually takes effect.
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Never cached: the sync endpoints (they must always hit the network), the
// notification poll (JSON, changes every few seconds) and anything auth-related
// — a cached login or logout page is how a session ends up looking wrong.
const NO_CACHE = ['/offline/sync/', '/offline/ping/', '/accounts/', '/admin/'];

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // never cache writes
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // leave cross-origin alone
  if (NO_CACHE.some((p) => url.pathname.startsWith(p))) return;

  // Static assets: cache-first (they are versioned by ?v= query strings).
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      }))
    );
    return;
  }

  // Pages: network-first, fall back to the last cached copy, then the offline page.
  e.respondWith(
    fetch(req).then((res) => {
      // Don't cache a redirect to the login page under the URL that was asked
      // for — offline that would show a sign-in screen where a ward list should
      // be, and the staff would think the app had logged them out.
      if (res.ok && !res.redirected) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() =>
      // `ignoreSearch` so a filtered list (/patients/?q=ali) falls back to the
      // cached /patients/ rather than the offline page — the rows are already
      // there and the client filters them without the server.
      caches.match(req, { ignoreSearch: true })
        .then((hit) => hit || caches.match('/offline/'))
    )
  );
});
"""


_OFFLINE_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offline — __NAME__</title>
<style>
  body{font-family:"Segoe UI",Arial,sans-serif;background:#f3f4f6;color:#1f2937;
       display:grid;place-items:center;height:100vh;margin:0;text-align:center;padding:20px}
  .card{background:#fff;border-radius:16px;padding:36px 30px;max-width:380px;
        box-shadow:0 4px 24px rgba(0,0,0,.08)}
  .dot{width:56px;height:56px;border-radius:50%;background:__THEME__;margin:0 auto 16px;
       display:grid;place-items:center;color:#fff;font-size:26px}
  h1{font-size:20px;margin:0 0 8px} p{color:#6b7280;font-size:14px;line-height:1.6;margin:0 0 18px}
  button{background:__THEME__;color:#fff;border:none;border-radius:10px;padding:11px 22px;
         font-size:15px;font-weight:600;cursor:pointer}
</style></head><body>
  <div class="card">
    <div class="dot">📶</div>
    <h1>You're offline</h1>
    <p>This page hasn't been opened on this device yet, so there's nothing saved to
       show. Pages you have already visited still work, and anything you enter is
       saved here and sent to __NAME__ automatically once the internet is back.</p>
    <button onclick="location.reload()">Try again</button>
  </div>
</body></html>"""
