from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from inventory.views import dashboard
from user_mgmt.views import setup_wizard
from user_mgmt import pwa_views
from django.conf import settings
from django.conf.urls.static import static
from saas.views import hospital_login
from accounts.views import demo_login


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
    path('saas/', include('saas.urls')),
    path('setup/', setup_wizard, name='setup'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    # One-click public demo. Explicit route so it wins over the <hospital_slug>
    # catch-all at the bottom of this list.
    path('demo/', demo_login, name='demo_login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', dashboard, name='dashboard'),
    path('dashboard/', dashboard, name='dashboard_page'),
    path('medicines/', include('inventory.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('sales/', include('sales.urls')),
    path('customers/', include('customers.urls')),
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
    path('manage/audit/', include('audit.urls')),
    path('accounts/', include('accounts.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('<slug:hospital_slug>/', hospital_login, name='hospital_login_landing'),
    path('<slug:hospital_slug>/login/', hospital_login, name='hospital_login'),
]


# Serve uploaded media through Django. On the desktop app (waitress) this is the only
# thing serving media; on PythonAnywhere the proxy maps /media/ first, so this is just a
# harmless fallback. Static files are handled by WhiteNoise (see settings).
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


