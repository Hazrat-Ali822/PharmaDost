from django.shortcuts import redirect, render
from django.urls import resolve, Resolver404
from django.utils import timezone
from .models import Hospital

class HospitalSubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .utils import hospital_from_host

        # 1. Path exclusions (logout, static, media, saas portal, public portal, etc.)
        path = request.path
        exclude_prefixes = [
            '/logout/', '/login/', '/accounts/', '/saas/', '/admin/', '/static/', '/media/', '/portal/', '/opd/track/'
        ]
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            return self.get_response(request)

        # 2. Skip checks for superusers
        if getattr(request.user, 'is_superuser', False):
            return self.get_response(request)

        # 3. Determine the target hospital:
        # Prioritize host hospital (subdomain) if accessing via hospital subdomain, else user's hospital
        host_hospital = hospital_from_host(request.get_host())

        # If user is authenticated and accessing another hospital's subdomain, enforce strict domain isolation
        if request.user.is_authenticated and not request.user.is_superuser:
            if host_hospital and getattr(request.user, 'hospital_id', None) != host_hospital.id:
                from django.contrib.auth import logout
                logout(request)
                return redirect('login')

        hospital = host_hospital or getattr(request.user, 'hospital', None)
        if not hospital:
            return self.get_response(request)

        # Hospital login paths exclusion
        hospital_login_path = f"/{hospital.slug}/"
        if path == hospital_login_path or path == f"{hospital_login_path}login/":
            return self.get_response(request)

        # Demo hospital never expires
        if hospital.slug in ('demo', 'sehatyar-demo-hospital'):
            return self.get_response(request)

        # 4. Check if subscription is expired or suspended for THIS specific hospital
        today = timezone.now().date()
        if not hospital.is_active or (hospital.expiry_date and hospital.expiry_date < today):
            return render(request, 'saas/suspended.html', {'hospital': hospital})

        # 5. Check if subscription is expiring in 5 days or less
        if hospital.expiry_date:
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
