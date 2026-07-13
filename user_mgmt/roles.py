from functools import wraps
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required


ROLE_SUPER = 'Superuser'
ROLE_ADMIN = 'ADMIN'
ROLE_RECEPTIONIST = 'RECEPTIONIST'
ROLE_DOCTOR = 'DOCTOR'
ROLE_PHARMACIST = 'PHARMACIST'
ROLE_WHOLESALE = 'WHOLESALE'
ROLE_LABTECH = 'LABTECH'
ROLE_SONOGRAPHER = 'SONOGRAPHER'
ROLE_ACCOUNTANT = 'ACCOUNTANT'

ALLOWED_ROLES = {
    ROLE_ADMIN,
    ROLE_RECEPTIONIST,
    ROLE_DOCTOR,
    ROLE_PHARMACIST,
    ROLE_WHOLESALE,
    ROLE_LABTECH,
    ROLE_SONOGRAPHER,
    ROLE_ACCOUNTANT,
}


def user_has_role(user, roles):
    if user.is_superuser:
        return True
    return user.is_authenticated and getattr(user, 'role', None) in roles


def roles_required(*roles):
    def deco(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if user_has_role(request.user, roles):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden('Not allowed for your role')
        return _wrapped
    return deco