from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


# The two sentences a blocked user actually sees. Written for a nurse or a
# receptionist, so neither of them names a feature key: "you do not hold
# ward_manage" means nothing to the person reading it.
NO_MODULE = ("Your hospital has not turned this part of the system on.")
NO_ACCESS = ("This screen belongs to a different job in the hospital, so your "
             "account cannot open it.")


def denied(request, reason=NO_ACCESS):
	"""HTTP 403, rendered as a real page instead of a wall of black serif text.

	`HttpResponseForbidden("...")` returns that bare string as the whole document
	— no shell, no branding, no way back. A browser QA pass over all nine roles
	found twenty *visible* links (sidebar items, dashboard tiles, header buttons)
	that land here, and on that page every one of them reads as a crash rather
	than a permission message. The status code is unchanged; only what the human
	sees is.

	Falls back to the bare response if the shell itself cannot render — an error
	page that can raise its own error is worse than an ugly one.
	"""
	try:
		return render(request, '403.html', {'reason': reason}, status=403)
	except Exception:
		from django.http import HttpResponseForbidden
		return HttpResponseForbidden(reason)


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
				return denied(request, NO_MODULE)
			if getattr(user, 'is_superuser', False):
				return view_func(request, *args, **kwargs)
			if any(user_has_feature(user, f) for f in allowed):
				return view_func(request, *args, **kwargs)
			return denied(request)
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
				return denied(request, NO_MODULE)
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
			return denied(request)
		return _wrapped
	return decorator