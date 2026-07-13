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