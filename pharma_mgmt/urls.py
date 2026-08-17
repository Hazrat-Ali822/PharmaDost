from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from inventory.views import dashboard
from user_mgmt.views import setup_wizard
from user_mgmt import pwa_views
from user_mgmt import seo_views
from django.conf import settings
from django.conf.urls.static import static
from saas.views import hospital_login
from accounts.views import demo_login, smart_login, custom_password_reset_request


urlpatterns = [
    path('admin/', admin.site.urls),
    # PWA (install-as-app). The service worker MUST be at the root so its scope
    # covers every page; the rest sit alongside it for tidiness.
    path('sw.js', pwa_views.service_worker, name='pwa_service_worker'),
    path('manifest.webmanifest', pwa_views.manifest, name='pwa_manifest'),
    path('app-icon-<int:size>.png', pwa_views.icon, name='pwa_icon'),
    path('offline/', pwa_views.offline, name='pwa_offline'),
    path('get-app/', pwa_views.get_app, name='get_app'),
    # Desktop/LAN build only: the address other devices on the wifi join on.
    path('get-app/connect/', pwa_views.lan_connect, name='lan_connect'),
    # Offline outbox replay — the browser POSTs its queued actions to
    # /offline/sync/ when it comes back online (authenticated, same-origin;
    # not a headless job). Sits after pwa_offline so /offline/ still serves the
    # offline page and only /offline/sync/ falls through to this include.
    path('offline/', include('offline_sync.urls')),
    path('messages/', include('messaging.urls')),
    # Public SEO / AEO surface (crawlable, no login) — the marketing home a search
    # engine or AI answer engine can index and cite. See user_mgmt.seo_views.
    path('features/', seo_views.landing, name='seo_landing'),
    path('robots.txt', seo_views.robots_txt, name='robots_txt'),
    path('sitemap.xml', seo_views.sitemap_xml, name='sitemap_xml'),
    path('llms.txt', seo_views.llms_txt, name='llms_txt'),
    # Keyword-targeted content pages — top-level slugs, so they MUST sit above the
    # <slug:hospital_slug> catch-all below or the slug pattern would swallow them.
    *[path(f'{slug}/', seo_views.content_page, {'slug': slug}, name=f'seo_page_{slug}')
      for slug in seo_views.CONTENT_PAGES],
    path('saas/', include('saas.urls')),
    path('setup/', setup_wizard, name='setup'),
    # Host-aware login: the bare platform domain shows the SaaS-owner sign-in
    # (+ demo); a hospital subdomain (<slug>.<BASE_DOMAIN>) shows that tenant's
    # branded, isolated login. See accounts.views.smart_login.
    path('login/', smart_login, name='login'),
    # One-click public demo. Explicit route so it wins over the <hospital_slug>
    # catch-all at the bottom of this list.
    path('demo/', demo_login, name='demo_login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    # Root: dashboard for signed-in users, the public marketing landing for an
    # anonymous visitor on the bare platform domain (so the homepage a crawler
    # indexes is real content, not a login wall). See user_mgmt.seo_views.home.
    path('', seo_views.home, name='dashboard'),
    path('dashboard/', dashboard, name='dashboard_page'),
    path('medicines/', include('inventory.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('sales/', include('sales.urls')),
    path('customers/', include('customers.urls')),
    path('panels/', include('panels.urls')),
    path('reports/', include('reports.urls')),
    path('patients/', include('patients.urls')),
    path('opd/', include('opd.urls')),
    path('billing/', include('billing.urls')),
    path('prescriptions/', include('prescriptions.urls')),
    path('manage/', include('user_mgmt.urls')),
    path('lab/', include('lab.urls')),
    path('imaging/', include('imaging.urls')),
    path('ipd/', include('ipd.urls')),
    path('ot/', include('ot.urls')),
    path('emergency/', include('emergency.urls')),
    path('hr/', include('hr.urls')),
    path('maternity/', include('maternity.urls')),
    path('diagnosis/', include('diagnosis.urls')),
    path('referral/', include('referral.urls')),
    path('ambulance/', include('ambulance.urls')),
    path('certificates/', include('certificates.urls')),
    path('bloodbank/', include('bloodbank.urls')),
    path('vaccination/', include('vaccination.urls')),
    path('consent/', include('consent.urls')),
    path('manage/audit/', include('audit.urls')),
    path('accounts/', include('accounts.urls')),
    # django.contrib.auth.urls ALSO registers a view named 'login' at
    # /accounts/login/, and reverse('login') resolves there — so the login form
    # (action="{% url 'login' %}") and LoginRequiredMiddleware would POST to the
    # plain LoginView, bypassing the host-aware smart_login and its tenant
    # isolation. Shadow it here (before the include) so /accounts/login/ is
    # handled by smart_login too — now every login path routes through it.
    path('accounts/login/', smart_login, name='login'),
    path('accounts/password_reset/', custom_password_reset_request, name='password_reset'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('<slug:hospital_slug>/', hospital_login, name='hospital_login_landing'),
    path('<slug:hospital_slug>/login/', hospital_login, name='hospital_login'),
]


# Serve uploaded media through Django. On the desktop app (waitress) this is the only
# thing serving media; on PythonAnywhere the proxy maps /media/ first, so this is just a
# harmless fallback. Static files are handled by WhiteNoise (see settings).
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


