from django.shortcuts import redirect, render
from django.urls import resolve, Resolver404
from django.utils import timezone
from .models import Hospital

class HospitalSubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Skip checks for unauthenticated users or superusers
        if not request.user.is_authenticated or request.user.is_superuser:
            return self.get_response(request)

        # 2. Skip checks if user doesn't belong to a hospital
        hospital = request.user.hospital
        if not hospital:
            return self.get_response(request)

        # 3. Define path exclusions (logout, static, media, saas portal, etc.)
        path = request.path
        exclude_prefixes = [
            '/logout/', '/login/', '/accounts/', '/saas/', '/admin/', '/static/', '/media/'
        ]
        
        # Check if current path matches exclusions
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            return self.get_response(request)

        # Also exclude the hospital-specific login paths to prevent redirect loops
        hospital_login_path = f"/{hospital.slug}/"
        if path == hospital_login_path or path == f"{hospital_login_path}login/":
            return self.get_response(request)

        # 4. Check if subscription is expired or suspended
        today = timezone.now().date()
        if not hospital.is_active or hospital.expiry_date < today:
            return render(request, 'saas/suspended.html', {'hospital': hospital})

        # 5. Check if subscription is expiring in 5 days or less
        days_left = (hospital.expiry_date - today).days
        if 0 <= days_left <= 5:
            request.subscription_warning = True
            request.subscription_days_left = days_left
        else:
            request.subscription_warning = False

        return self.get_response(request)


from .utils import set_current_hospital, set_tenant_strict, clear_current_hospital

class TenantMiddleware:
    """Binds the request's hospital to the thread for `TenantManager` to filter on.

    Also sets the "strict" flag for every authenticated non-superuser, so that a
    user with no hospital sees only hospital-less rows instead of every tenant's
    data. Superusers (and anonymous requests, which get bounced to login anyway)
    stay unrestricted so the SaaS portal can work across tenants.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        authenticated = user.is_authenticated
        set_current_hospital(getattr(user, 'hospital', None) if authenticated else None)
        set_tenant_strict(authenticated and not user.is_superuser)

        try:
            response = self.get_response(request)
        finally:
            clear_current_hospital()

        return response
