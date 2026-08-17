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


def module_installed(*features: str):
	"""Require that ALL the given features are installed for this tenant.

	`feature_required` passes on ANY key and is about *who* may open a screen.
	This is about *whether the tenant bought the module at all*, and it stacks on
	top — the lab/imaging price editors are gated on the CORE `catalog` feature,
	which is on for every install, so a pharmacy-only tenant could open a lab test
	price list for a lab it does not have. Like the install check in
	`feature_required`, a superuser does not bypass this: the module is off for
	the tenant, not merely out of reach for the user.
	"""
	def decorator(view_func):
		@wraps(view_func)
		def _wrapped(request, *args, **kwargs):
			from .permissions import installed_features
			inst = installed_features()
			if any(f not in inst for f in features):
				return HttpResponseForbidden("This module is turned off for this system.")
			return view_func(request, *args, **kwargs)
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