from django.shortcuts import redirect
from django.conf import settings
from django.urls import resolve


class SetupMiddleware:
	"""On a fresh install (no users yet), force the first-run setup wizard.
	Once any user exists this becomes a no-op (cached), so no per-request query."""

	_configured = False

	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		if not SetupMiddleware._configured:
			from accounts.models import User
			if User.objects.exists():
				SetupMiddleware._configured = True
			else:
				path = request.path
				if not (path.startswith('/setup') or path.startswith('/static/')
						or path.startswith('/media/')):
					return redirect('setup')
		return self.get_response(request)


ALLOWED_NAMES = {
'login', 'logout', 'password_change', 'password_change_done',
'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
'admin:login', 'admin:index',
'hospital_login', 'hospital_login_landing',
'demo_login',
# Public SEO / AEO pages — a search engine or AI crawler must reach these
# without a session, or the site has nothing to index and login-walls them.
# 'dashboard' is the root '/': its view (seo_views.home) serves the marketing
# landing to anonymous visitors and the app dashboard to signed-in ones.
'dashboard', 'seo_landing', 'robots_txt', 'sitemap_xml', 'llms_txt',
# The desktop/LAN cloud-backup upload is called by the launcher (no browser
# session, authenticated by its signed licence), so it must not redirect to login.
'saas:backup_upload',
# PWA plumbing the browser fetches without a session (install prompt, offline
# fallback) — these must never redirect to login or the install breaks.
'pwa_service_worker', 'pwa_manifest', 'pwa_icon', 'pwa_offline',
}


class LoginRequiredMiddleware:
	"""Redirect anonymous users to LOGIN_URL, except for auth/admin/static/media."""

	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		path = request.path
		if (path.startswith('/static/') or path.startswith('/media/')
				or path.startswith('/admin/') or path.startswith('/setup')):
			return self.get_response(request)
		try:
			match = resolve(path)
			if match.view_name in ALLOWED_NAMES or path.startswith('/accounts/'):
				return self.get_response(request)
		except Exception:
			pass
		if request.user.is_authenticated:
			return self.get_response(request)
		return redirect(settings.LOGIN_URL)


class DesktopLicenseMiddleware:
	"""Offline monthly-subscription lock for the desktop / clinic-LAN build.

	A no-op on the hosted SaaS site (``DESKTOP_BUILD`` is False there) — hosted
	tenants are gated by ``Hospital.expiry_date`` in ``HospitalSubscriptionMiddleware``.
	On the desktop build it checks the signed licence (``licensing.core``) every
	request: inside the licence or the trial the app runs (a banner shows when the
	end is near); once expired or the trial is over, every screen is replaced with a
	lock page until an admin pastes a fresh key in Settings → Licence. Because every
	phone on the clinic LAN goes through this one server, the lock reaches them all.
	"""

	_allow_prefixes = ('/static/', '/media/', '/accounts/')

	def __init__(self, get_response):
		self.get_response = get_response
		self._license_path = None

	def _license_url(self):
		if self._license_path is None:
			from django.urls import reverse
			try:
				self._license_path = reverse('user_mgmt:license')
			except Exception:
				self._license_path = '/manage/license/'
		return self._license_path

	def __call__(self, request):
		if not getattr(settings, 'DESKTOP_BUILD', False):
			return self.get_response(request)

		from user_mgmt import licensing as core
		state = core.license_state(settings.DATA_DIR)
		request.license_state = state       # base.html reads this for the banner
		if state['ok']:
			return self.get_response(request)

		# Locked: allow only what an admin needs to sign back in and paste a key.
		path = request.path
		if (path.startswith(self._allow_prefixes)
				or path == self._license_url()
				or path.endswith('/login/') or path.endswith('/logout/')):
			return self.get_response(request)

		from django.shortcuts import render
		return render(request, 'desktop/license_locked.html',
					  {'license': state}, status=402)