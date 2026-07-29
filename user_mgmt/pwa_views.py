"""Progressive Web App plumbing: a per-tenant manifest, icon, service worker and
offline page, so the site installs as an app carrying each hospital's own name,
logo and colour — "Shaheen Health Care", not "PharmaDost".

Everything here reads `SiteSettings.load()`, which resolves the current tenant
from the logged-in user, so the installed app is branded per hospital. The
requests carry the session cookie (same-origin), so tenancy is in scope.

Honest scope note: the service worker caches the *shell* so the app opens and
shows recently-loaded pages on a dropped connection, with a clear offline banner.
It does NOT do offline transactional writes with sync-back — stock, MRN and token
numbers are server-authoritative, and queuing those offline would oversell stock
and clash numbers. True fully-offline use is the desktop build (local SQLite).
"""
import hashlib
import io

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
    name = b.brand_name or "PharmaDost"
    data = {
        "name": name,
        "short_name": (name[:12] or "PharmaDost").strip(),
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
        letter = (b.logo_text or (b.brand_name or "P")[0] or "P")[:2].upper()
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


def service_worker(request):
    """Served at /sw.js (root) so its scope covers the whole site — a service
    worker can only control pages at or below its own URL."""
    version = hashlib.md5(
        f"{_branding().pk}:{_branding().updated_at}".encode()).hexdigest()[:8]
    body = _SERVICE_WORKER.replace("__VERSION__", version)
    resp = HttpResponse(body, content_type="application/javascript")
    # The SW file itself must never be cached, or updates never reach clients.
    resp["Cache-Control"] = "no-cache"
    return resp


def offline(request):
    """Shown by the service worker when a page is requested with no connection."""
    return HttpResponse(_OFFLINE_PAGE.replace("__THEME__", _theme(_branding()))
                        .replace("__NAME__", _branding().brand_name or "PharmaDost"),
                        content_type="text/html")


from accounts.decorators import feature_required  # noqa: E402


def get_app(request):
    """The 'Get the App' page — an Install button plus per-platform instructions."""
    from django.shortcuts import render
    return render(request, "pwa/get_app.html")


# --- The service worker itself (network-first for pages, cache-first for assets) ---
_SERVICE_WORKER = r"""
const CACHE = 'pharmadost-__VERSION__';
const SHELL = ['/static/css/app.css', '/offline/'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  // Drop old versions so a redeploy actually takes effect.
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // never cache writes
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // leave cross-origin alone

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
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }).catch(() =>
      caches.match(req).then((hit) => hit || caches.match('/offline/'))
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
    <p>__NAME__ needs a connection for live data — stock, tokens and bills come from
       the server. Pages you already opened still work. We'll reconnect on their own
       once the internet is back.</p>
    <button onclick="location.reload()">Try again</button>
  </div>
</body></html>"""
