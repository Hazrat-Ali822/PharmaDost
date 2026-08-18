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
'demo_login', 'demo_login_as',
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
# The fingerprint terminal. It is a machine on a wall, not a browser: no
# session, no cookies, authenticated by its serial. A redirect to the login
# page is a 302 it does not follow, so the punches simply never arrive and
# nothing anywhere says so.
'biometric_cdata', 'biometric_getrequest', 'biometric_devicecmd',
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
			# `seo_page_*` are the public keyword content pages (seo_views.content_page).
			if (match.view_name in ALLOWED_NAMES or path.startswith('/accounts/')
					or (match.view_name or '').startswith('seo_page_')):
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

# --- freshness -------------------------------------------------------------
# Cookie holding a token that changes whenever anything is written. Not secret,
# and JS must read it, so no httponly.
DATA_VERSION_COOKIE = 'dv'

# Writes that change nothing a page displays. Bumping the token for these would
# make every Back tap re-fetch after a bell had merely been marked read.
_DV_SKIP_PREFIXES = ('/accounts/notifications/', '/offline/ping/')


def _dv_token():
	from django.utils.crypto import get_random_string
	return get_random_string(8)


class DataVersionMiddleware:
	"""Make Back show the *current* page instead of the one from before the edit.

	Browsers restore a back-navigation from the bfcache: the page comes back from
	memory exactly as it was, with no request to the server. So a bill that read
	Rs 600 before a test was cancelled still read Rs 600 after it, until the user
	pressed refresh — which is what a receptionist reports as "the amount didn't
	change". Nothing server-side can fix that; the server is never asked.

	So the server stamps a token: this middleware issues a **new** `dv` cookie
	after every successful write, and `partials/base.html` renders the token the
	page was built with into `<body data-dv>`. On `pageshow`/`visibilitychange`
	the page compares the two and re-fetches **only when they differ**. A Back with
	nothing changed in between stays instant — no request at all — which is the
	whole point of doing it this way rather than blanket `no-store`.

	The token is planted into `request.COOKIES` before the view runs on a first
	visit, so the very first page already carries the same value the browser is
	about to be given and does not immediately think itself stale.
	"""

	_SAFE = frozenset(('GET', 'HEAD', 'OPTIONS', 'TRACE'))

	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		issue = None
		if not request.COOKIES.get(DATA_VERSION_COOKIE):
			issue = _dv_token()
			request.COOKIES[DATA_VERSION_COOKIE] = issue

		response = self.get_response(request)

		# Any non-erroring write bumps it — including a form that came back with
		# validation errors, which wrote nothing. Distinguishing the two is not
		# worth it: an HTTP 200 from a POST is a re-rendered form *usually*, not
		# always, and guessing wrong in the other direction brings the original
		# bug back. Over-bumping costs one extra fetch the next time the user
		# presses Back; under-bumping shows them a bill that is no longer true.
		if (request.method not in self._SAFE
				and response.status_code < 400
				and not request.path.startswith(_DV_SKIP_PREFIXES)):
			issue = _dv_token()

		if issue:
			response.set_cookie(
				DATA_VERSION_COOKIE, issue,
				max_age=60 * 60 * 24 * 30, samesite='Lax', httponly=False,
				secure=getattr(settings, 'SESSION_COOKIE_SECURE', False))
		return response
