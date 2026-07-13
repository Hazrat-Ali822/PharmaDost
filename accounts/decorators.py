from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden


def feature_required(*features: str):
	"""Gate a view to users who have ANY of the given feature keys.

	Feature membership is role-default OR per-user custom override (see
	accounts.permissions). Superusers always pass.
	"""
	def decorator(view_func):
		@login_required
		@wraps(view_func)
		def _wrapped(request, *args, **kwargs):
			from .permissions import user_has_feature, installed_features
			user = request.user
			# module must be turned on for this install
			inst = installed_features()
			allowed = [f for f in features if f in inst]
			if not allowed:
				return HttpResponseForbidden("This module is turned off for this system.")
			if getattr(user, 'is_superuser', False):
				return view_func(request, *args, **kwargs)
			if any(user_has_feature(user, f) for f in allowed):
				return view_func(request, *args, **kwargs)
			return HttpResponseForbidden("You do not have permission to access this page.")
		return _wrapped
	return decorator


def role_required(allowed_roles: list[str]):
	"""Gate a view to specific user roles. allowed_roles like ["ADMIN", "PHARMACIST"]."""
	def decorator(view_func):
		@login_required
		@wraps(view_func)
		def _wrapped(request, *args, **kwargs):
			user = request.user
			# superuser always allowed
			if getattr(user, 'is_superuser', False):
				return view_func(request, *args, **kwargs)
			role = getattr(user, 'role', None)
			if role in allowed_roles:
				return view_func(request, *args, **kwargs)
			return HttpResponseForbidden("You do not have permission to access this page.")
		return _wrapped
	return decorator