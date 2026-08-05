"""Adds a `nav` dict of role-based sidebar permissions to every template.

Kept in sync with the role_required gating on the actual views so users only
see links they can actually open.
"""

from .permissions import FEATURES, user_has_feature, installed_features


def nav_permissions(request):
    """Sidebar visibility, derived from the same feature checks the views use —
    a link shows only if the module is installed AND the user has access."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'nav': {}}
    inst = installed_features()
    nav = {key: (key in inst and user_has_feature(user, key)) for key in FEATURES}
    return {'nav': nav}


def site_branding(request):
    """Expose editable branding (name, logo, colours) to every template.

    Uses the key `branding` (NOT `site`) because Django's auth LoginView injects
    its own `site` context variable, which would otherwise shadow ours.
    """
    import os

    from user_mgmt.models import SiteSettings, SITE_DEFAULTS
    try:
        branding = SiteSettings.load()
    except Exception:
        branding = None

    # The app icon, addressed by tenant rather than by session. Safari fetches
    # apple-touch-icon through its own loader without the session cookie, so a
    # plain {% url 'pwa_icon' %} renders the platform default and the installed
    # app on the home screen carries the wrong logo.
    icon_url = ''
    if branding is not None:
        try:
            from user_mgmt.pwa_views import brand_token
            from django.urls import reverse
            icon_url = f"{reverse('pwa_icon', args=[192])}?t={brand_token(branding)}"
        except Exception:
            icon_url = ''

    return {
        'branding': branding,
        # short handle for the currency symbol, used in front of every amount.
        # Defaults to Rs so a template still renders when branding can't be loaded.
        'currency': (branding.currency_symbol if branding else 'Rs') or 'Rs',
        'brand_icon_url': icon_url,
        'site_defaults': SITE_DEFAULTS,
        # Set only by the desktop build acting as a LAN server (see
        # desktop/launcher.py). An env read, not a query — this runs on every
        # render. Empty on the hosted site, which hides the sidebar link.
        'lan_url': os.environ.get('PHARMADOST_LAN_URL', ''),
    }


BADGE_KEYS = (
    'opd_badge_count', 'lab_badge_count', 'imaging_badge_count', 'ipd_badge_count',
    'ot_badge_count', 'pharmacy_badge_count', 'admission_request_count',
    'surgery_request_count',
)

# How long a sidebar badge may lag reality. Badges are an at-a-glance hint, not an
# alert — the notification poll covers anything urgent — so trading a little
# freshness for eight fewer COUNT queries on every page load is worth it.
BADGE_CACHE_SECONDS = 30


def _badge_counts(user):
    """The eight sidebar counters, scoped to the user's hospital and to the
    modules they can actually see.

    This runs on EVERY page render, so it is deliberately frugal: a count is only
    queried when its module is installed AND the user holds the feature (a
    pharmacist never pays for the OPD, lab, imaging, IPD and OT counts), and the
    whole set is cached briefly per user.
    """
    from django.core.cache import cache
    from .permissions import installed_features, user_has_feature

    hospital = getattr(user, 'hospital', None)
    cache_key = f'badges:{user.pk}:{getattr(hospital, "pk", None)}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    counts = dict.fromkeys(BADGE_KEYS, 0)

    inst = installed_features()

    def visible(feature):
        return feature in inst and user_has_feature(user, feature)

    # Fail closed: every non-superuser's badge counts are scoped to their own
    # hospital (None → only hospital-less rows), never another tenant's.
    scope_by_hospital = not user.is_superuser
    role = getattr(user, 'role', None)
    is_doctor = role == 'DOCTOR' and not user.is_superuser

    try:
        if visible('opd'):
            from opd.models import Appointment
            qs = Appointment.objects.all()
            if scope_by_hospital:
                qs = qs.filter(patient__hospital=hospital)
            qs = qs.exclude(status__in=['DONE', 'CANCELLED'])
            if is_doctor:
                qs = qs.filter(doctor__user=user)
            counts['opd_badge_count'] = qs.count()

        if visible('lab'):
            from lab.models import TestOrder
            qs = TestOrder.objects.filter(status='Pending')
            if scope_by_hospital:
                qs = qs.filter(patient__hospital=hospital)
            if is_doctor:
                qs = qs.filter(ordered_by=user)
            counts['lab_badge_count'] = qs.count()

        if visible('imaging'):
            from imaging.models import ImagingStudy
            qs = ImagingStudy.objects.filter(status='Pending')
            if scope_by_hospital:
                qs = qs.filter(patient__hospital=hospital)
            if is_doctor:
                qs = qs.filter(referred_by=user)
            counts['imaging_badge_count'] = qs.count()

        if visible('ipd') or visible('ward'):
            from ipd.models import Admission
            qs = Admission.objects.filter(status='Admitted')
            if scope_by_hospital:
                qs = qs.filter(hospital=hospital)
            counts['ipd_badge_count'] = qs.count()

        if visible('ipd'):
            from ipd.models import AdmissionRequest
            qs = AdmissionRequest.objects.filter(status='Pending')
            if scope_by_hospital:
                qs = qs.filter(hospital=hospital)
            counts['admission_request_count'] = qs.count()

        if visible('ot'):
            from ot.models import SurgeryRecord, SurgeryRequest
            qs = SurgeryRecord.objects.filter(end_time__isnull=True)
            if scope_by_hospital:
                qs = qs.filter(hospital=hospital)
            counts['ot_badge_count'] = qs.count()

            qs = SurgeryRequest.objects.filter(status='Pending')
            if scope_by_hospital:
                qs = qs.filter(hospital=hospital)
            counts['surgery_request_count'] = qs.count()

        if visible('pos'):
            from prescriptions.models import Prescription
            qs = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL'])
            if scope_by_hospital:
                qs = qs.filter(appointment__patient__hospital=hospital)
            counts['pharmacy_badge_count'] = qs.count()
    except Exception:
        pass

    cache.set(cache_key, counts, BADGE_CACHE_SECONDS)
    return counts


def notifications_context(request):
    """Unread notifications plus the sidebar's pending-task counters."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'unread_notifications': [],
            'unread_notifications_count': 0,
            **dict.fromkeys(BADGE_KEYS, 0),
        }

    from .models import Notification
    unread = Notification.objects.filter(user=user, is_read=False).order_by('-created_at')[:5]
    unread_count = Notification.objects.filter(user=user, is_read=False).count()

    return {
        'unread_notifications': unread,
        'unread_notifications_count': unread_count,
        **_badge_counts(user),
    }
