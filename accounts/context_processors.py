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
    from user_mgmt.models import SiteSettings, SITE_DEFAULTS
    try:
        branding = SiteSettings.load()
    except Exception:
        branding = None
    return {'branding': branding, 'site_defaults': SITE_DEFAULTS}
