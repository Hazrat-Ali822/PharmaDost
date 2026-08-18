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

from django.core import signing
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control

from .models import SiteSettings


def _branding():
    return SiteSettings.load()


def _theme(branding):
    return branding.primary_color or "#4f46e5"


def brand_token(branding):
    """An opaque, stable handle for one tenant's branding row.

    The browser fetches the manifest's icons, and Safari the apple-touch-icon,
    through its own image loader — **not always with the session cookie** — so an
    icon endpoint that resolves the tenant from `request.user` renders the
    platform default and the installed app ends up with the wrong logo. Naming
    the branding row in the URL removes the dependency on cookies entirely.

    Signed rather than a bare primary key so the endpoint cannot be walked to
    pull every tenant's logo out of a shared host, and `Signer` (not
    `dumps`, which stamps a timestamp) so the same tenant yields the *same*
    string every request — a URL that changed each load would defeat the icon
    cache. `updated_at` is folded in so replacing the logo changes the URL and
    the new one actually appears.
    """
    # isoformat, not a whole-second stamp: two edits inside the same second
    # would otherwise share a URL and the second logo would never appear.
    stamp = branding.updated_at.isoformat() if branding.updated_at else '0'
    return signing.Signer(salt=_ICON_SALT).sign(f"{branding.pk}:{stamp}")


def _branding_from_token(token):
    """The branding row a `brand_token` refers to, or None if it doesn't."""
    try:
        value = signing.Signer(salt=_ICON_SALT).unsign(token)
    except (signing.BadSignature, TypeError):
        return None
    return SiteSettings.objects.filter(pk=value.split(":")[0]).first()


_ICON_SALT = "pwa-icon"


def _icon_url(size, token):
    return f"{reverse('pwa_icon', args=[size])}?t={token}"


def manifest(request):
    """The web app manifest — what a browser reads to install the site as an app.

    Reaching the right tenant here needs `crossorigin="use-credentials"` on the
    `<link rel="manifest">` (see partials/base.html): a manifest is fetched with
    *no* cookies by default, which is why every install carried the platform
    name "Sehatyar" instead of the hospital's own.
    """
    b = _branding()
    name = b.brand_name or "Sehatyar"
    token = brand_token(b)
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
            {"src": _icon_url(192, token), "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": _icon_url(512, token), "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    }
    resp = JsonResponse(data, content_type="application/manifest+json")
    # Two tenants share one host, so a shared cache must never hand one
    # hospital's manifest to another's staff.
    resp["Cache-Control"] = "private, max-age=0"
    return resp


@cache_control(max_age=3600)
def icon(request, size):
    """The app icon shown on the home screen / taskbar.

    Uses the tenant's uploaded logo when there is one; otherwise renders the
    brand letter on the theme colour, so an install always has a real icon rather
    than a broken square.

    `?t=` names the tenant (see `brand_token`) because this is fetched by the
    browser's image loader, which need not send the session cookie; the session
    is only the fallback.
    """
    from PIL import Image, ImageDraw, ImageFont

    size = 512 if int(size) >= 512 else 192
    b = _branding_from_token(request.GET.get("t", "")) or _branding()
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
        # No uploaded logo → the default Sehatyar logo (static/img/sehatyar-logo.png),
        # the same file every template shows, so the installed-app / favicon icon
        # matches the rest of the app.
        from django.contrib.staticfiles import finders
        default_path = finders.find("img/sehatyar-logo.png")
        if default_path:
            try:
                src = Image.open(default_path).convert("RGBA")
                canvas = Image.new("RGBA", (size, size), (255, 255, 255, 0))
                src.thumbnail((size, size), Image.LANCZOS)
                canvas.paste(src, ((size - src.width) // 2, (size - src.height) // 2), src)
                img = canvas.convert("RGB")
            except Exception:
                img = None

    if img is None:
        # Last resort if the default file is missing: draw the heart-and-pulse mark.
        img = Image.new("RGB", (size, size), _theme(b))
        draw = ImageDraw.Draw(img)
        s = size
        rl = 0.145 * s                                   # lobe radius
        lc, rc, ty = 0.40 * s, 0.60 * s, 0.40 * s        # lobe centres, top y
        draw.ellipse([lc - rl, ty - rl, lc + rl, ty + rl], fill="#ffffff")
        draw.ellipse([rc - rl, ty - rl, rc + rl, ty + rl], fill="#ffffff")
        draw.polygon([(lc - rl, 0.45 * s), (rc + rl, 0.45 * s), (0.50 * s, 0.72 * s)],
                     fill="#ffffff")
        pulse = [(0.30 * s, 0.50 * s), (0.40 * s, 0.50 * s), (0.45 * s, 0.39 * s),
                 (0.50 * s, 0.64 * s), (0.55 * s, 0.37 * s), (0.60 * s, 0.53 * s),
                 (0.70 * s, 0.50 * s)]
        draw.line(pulse, fill=_theme(b), width=max(3, int(0.038 * s)), joint="curve")

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
    'reception_desk', 'appointment_list', 'appointment_add', 'visit_create',
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
    'ipd:nursing_board', 'ipd:my_duties', 'ipd:duty_roster',
    'ipd:handover_board', 'ipd:ward_census',
    'ot:surgery_list', 'ot:surgery_create', 'ot:procedure_list',
    'invoice_list', 'expense_list', 'expense_create', 'cash_closing_new',
    # clinical add-ons now offline-capable (argless create/list pages; the
    # maternity antenatal form lives on a pk page, so it is cached on first visit
    # like the IPD bedside forms rather than pre-cached by name)
    'maternity_list', 'vaccination_list', 'diagnosis_list', 'referral_create',
    'consent_create', 'birth_create', 'death_create',
    'user_mgmt:help_center',
]

_SHELL_STATIC = ['/static/css/app.css', '/static/js/offline.js']

# The worker activates on these four alone — the app's own offline page, the
# screen an install opens on, and the two assets every page needs. Everything
# else in SHELL_URL_NAMES is fetched afterwards, in the background.
#
# This split is not a tidy-up. Blocking activation on all ~45 screens meant that
# on a clinic connection the worker was still installing minutes after the app
# was opened, and a user who turned the wifi off in that window had **no worker
# at all** — so every tap landed on the browser's own "you are offline" error
# page and the whole feature looked dead. Four requests arm it in about a second.
_CRITICAL_URL_NAMES = ['pwa_offline', 'dashboard']

# Bump when the worker's own source changes, so returning devices drop the old
# cache and re-warm instead of keeping a stale shell. Bump it for a change to
# `partials/base.html` too — the worker caches whole rendered pages, so every
# saved screen still carries the old chrome until its cache name changes.
# 6: back button + the `data-dv` staleness stamp.
# 7: sehatyar-logo.png lost its baked-in white background. Static assets are
#    cached cache-first and the logo carries no ?v=, so without a bump every
#    device that had already saved it would keep the white-cornered copy.
# 8: logo onerror fallback in base.html + app.css v2.7 (grid min-width fix).
_SW_REVISION = '18'


def _reverse_all(names):
    from django.urls import NoReverseMatch

    urls = []
    for name in names:
        try:
            urls.append(reverse(name))
        except NoReverseMatch:
            continue      # route removed — nothing to pre-cache, not an error
    return urls


def _critical_urls():
    return list(_SHELL_STATIC) + _reverse_all(_CRITICAL_URL_NAMES)


def _shell_urls():
    """Every URL the worker should end up holding, critical ones included."""
    critical = _critical_urls()
    rest = [u for u in _reverse_all(SHELL_URL_NAMES) if u not in critical]
    return critical + rest


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
    critical = _critical_urls()
    warm = [u for u in _shell_urls() if u not in critical]
    version = hashlib.md5(
        f"{b.pk}:{b.updated_at}:{who}:{len(critical) + len(warm)}:{_SW_REVISION}"
        .encode()).hexdigest()[:8]
    body = (_SERVICE_WORKER
            .replace("__VERSION__", version)
            .replace("__CRITICAL__", json.dumps(critical))
            .replace("__WARM__", json.dumps(warm))
            .replace("__SIGNED_IN__",
                     "true" if request.user.is_authenticated else "false")
            .replace("__OFFLINE_HTML__", json.dumps(_offline_html())))
    resp = HttpResponse(body, content_type="application/javascript")
    # The SW file itself must never be cached, or updates never reach clients.
    resp["Cache-Control"] = "no-cache"
    return resp


def _offline_html():
    b = _branding()
    return (_OFFLINE_PAGE.replace("__THEME__", _theme(b))
            .replace("__NAME__", b.brand_name or "Sehatyar"))


def offline(request):
    """Shown by the service worker when a page is requested with no connection."""
    return HttpResponse(_offline_html(), content_type="text/html")


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

// Both lists are built from Django's own url names (see SHELL_URL_NAMES) — a
// hand-written path that no longer resolves fails silently and the screen is
// simply not there when the connection drops, which is how `/opd/visit/` and
// `/lab/order/new/` sat in this list caching nothing. A URL the user's role
// cannot reach fails its `cache.add` and is skipped, which is fine.
const CRITICAL = __CRITICAL__;   // must be present before the worker takes over
const WARM = __WARM__;           // fetched afterwards, in the background
const OFFLINE_HTML = __OFFLINE_HTML__;
const SIGNED_IN = __SIGNED_IN__;

function offlinePage() {
  return new Response(OFFLINE_HTML, {
    status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

// Save one shell URL. NOT `cache.add()`, which follows redirects and then stores
// the final response under the *requested* URL: pre-caching while signed out
// therefore filed the login page under /sales/new/, /patients/ and every other
// screen, and the app then showed a sign-in form on each of them offline —
// indistinguishable, from the ward, from having been logged out.
async function save(c, url) {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok || res.redirected) return false;
  await c.put(url, res);
  return true;
}

// Install on the critical four only. Waiting here on every screen left the
// worker installing for minutes on a clinic connection, and a wifi drop in that
// window meant no worker existed at all — every tap then fell through to the
// browser's own error page, which is exactly what "offline doesn't work" looked
// like from the ward.
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(CRITICAL.map((url) => save(c, url).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  // Drop old versions so a redeploy actually takes effect. Nothing slow belongs
  // in here: fetch events are held until `activate` settles.
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// --- background warm-up -------------------------------------------------
// One at a time, not 45 at once: this runs on the same weak link the staff are
// using, and a burst of parallel requests makes the page they are actually
// looking at slower.
let warming = null;

function warmShell() {
  if (warming) return warming;
  // Signed out there is nothing worth saving — every screen would answer with a
  // redirect to the sign-in page, and ~45 requests would be spent proving it.
  if (!SIGNED_IN) return status();
  warming = caches.open(CACHE).then(async (c) => {
    for (const url of WARM) {
      if (await c.match(url)) continue;            // already saved
      try { await save(c, url); } catch (err) { /* offline, or role can't open it */ }
    }
    return status(c);
  }).then((s) => { warming = null; return s; },
          (err) => { warming = null; throw err; });
  return warming;
}

// Count the shell entries that are actually present, rather than everything in
// the cache: pages visited by hand and the ?v=-stamped stylesheet land in there
// too, and counting those reported more screens saved than there are.
async function status(c) {
  const cache = c || await caches.open(CACHE);
  const all = CRITICAL.concat(WARM);
  let saved = 0;
  for (const url of all) if (await cache.match(url)) saved++;
  return { type: 'status', saved: saved, total: all.length };
}

self.addEventListener('message', (e) => {
  const kind = e.data && e.data.type;
  if (kind !== 'warm' && kind !== 'status') return;
  const reply = (s) => { if (e.source) e.source.postMessage(s); };
  e.waitUntil((kind === 'warm' ? warmShell() : status()).then(reply, () => {}));
});

// --- fetch --------------------------------------------------------------
// Never cached: the sync endpoints (they must always hit the network), the
// notification poll (JSON, changes every few seconds) and anything auth-related
// — a cached login or logout page is how a session ends up looking wrong.
const NO_CACHE = ['/offline/sync/', '/offline/ping/', '/accounts/', '/admin/',
                  '/login/', '/logout/'];

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // never cache writes
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // leave cross-origin alone
  if (NO_CACHE.some((p) => url.pathname.startsWith(p))) return;
  // also the path-form tenant login/logout: /<slug>/login/ , /<slug>/logout/
  if (url.pathname.endsWith('/login/') || url.pathname.endsWith('/logout/')) return;

  // Static assets: cache-first. Templates link them with a ?v= cache-buster, so
  // an exact miss falls back to the same file without the query — after a
  // version bump that serves the previous copy offline instead of dropping the
  // stylesheet and rendering an unreadable page.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req)
        .then((hit) => hit || fetch(req).then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy).catch(() => {}));
          }
          return res;
        }))
        .catch(() => caches.match(req, { ignoreSearch: true })
          .then((hit) => hit || new Response('', { status: 504 })))
    );
    return;
  }

  // Pages: network-first, then the last cached copy, then the offline page.
  //
  // Every branch must end in a Response. `respondWith(undefined)` is a network
  // error, and the browser answers that with its own "you cannot reach this
  // site" page — the one screen this whole mechanism exists to prevent — so the
  // final fallback is built here rather than fetched from the cache.
  e.respondWith(
    fetch(req).then((res) => {
      // Don't cache a redirect to the login page under the URL that was asked
      // for — offline that would show a sign-in screen where a ward list should
      // be, and the staff would think the app had logged them out.
      if (res.ok && !res.redirected) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy).catch(() => {}));
      }
      return res;
    }).catch(() =>
      // `ignoreSearch` so a filtered list (/patients/?q=ali) falls back to the
      // cached /patients/ rather than the offline page — the rows are already
      // there and the client filters them without the server.
      caches.match(req, { ignoreSearch: true })
        .then((hit) => hit || caches.match('/offline/'))
        .then((hit) => hit || offlinePage())
        .catch(() => offlinePage())
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
