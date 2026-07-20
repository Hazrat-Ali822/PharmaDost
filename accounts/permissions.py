"""Single source of truth for feature-based access control.

Each *feature* is a module the app gates on. A role maps to a set of default
features; an individual user can override that with `custom_features` (a per-user
explicit list) — letting an admin grant extra access or take some away without
changing the role. Both the sidebar nav and the view decorators consult these
same helpers, so what a user sees always matches what they can open.
"""

# feature key -> set of roles that get it by default
FEATURES = {
    # Clinical
    'patients':      {'ADMIN', 'RECEPTIONIST', 'DOCTOR', 'LABTECH', 'SONOGRAPHER', 'ACCOUNTANT'},
    'opd':           {'ADMIN', 'RECEPTIONIST', 'DOCTOR'},
    'appointments':  {'ADMIN', 'RECEPTIONIST'},   # BOOK an appointment (doctor can't by default)
    'doctors':       {'ADMIN'},
    'prescriptions': {'ADMIN', 'DOCTOR', 'RECEPTIONIST'},
    'lab':           {'ADMIN', 'DOCTOR', 'LABTECH', 'RECEPTIONIST'},
    'imaging':       {'ADMIN', 'DOCTOR', 'SONOGRAPHER', 'RECEPTIONIST'},
    'ipd':           {'ADMIN', 'RECEPTIONIST', 'DOCTOR', 'ACCOUNTANT'},
    # ward/nursing work inside IPD (medication logs, nursing rounds, bed status) —
    # NOT admitting/discharging/billing. Given to nurses; admins have it too.
    'ward':          {'ADMIN', 'NURSE'},
    'ot':            {'ADMIN', 'DOCTOR'},
    # Pharmacy
    'pos':           {'ADMIN', 'PHARMACIST', 'WHOLESALE'},
    'inventory':     {'ADMIN', 'PHARMACIST'},
    'customers':     {'ADMIN', 'PHARMACIST', 'WHOLESALE', 'ACCOUNTANT', 'RECEPTIONIST'},
    'suppliers':     {'ADMIN', 'PHARMACIST', 'ACCOUNTANT'},
    # Finance
    'billing':       {'ADMIN', 'RECEPTIONIST', 'ACCOUNTANT'},
    'expenses':      {'ADMIN', 'ACCOUNTANT'},
    'cashclosing':   {'ADMIN', 'ACCOUNTANT'},
    'payouts':       {'ADMIN', 'ACCOUNTANT'},
    'reports':       {'ADMIN', 'PHARMACIST', 'ACCOUNTANT'},
    'profit':        {'ADMIN', 'ACCOUNTANT'},
    'daybook':       {'ADMIN', 'ACCOUNTANT'},
    # System
    'settings':      {'ADMIN'},
    'audit':         {'ADMIN'},
    # price list / service catalog management (lab test + scan prices) — admin only
    'catalog':       {'ADMIN'},
    # dashboard overview tile (not shown in the access editor)
    'overview':      {'ADMIN', 'PHARMACIST'},
}

# ordered, grouped, human-labelled — drives the access editor UI
FEATURE_GROUPS = [
    ('Clinical', [
        ('patients', 'Patients & History'),
        ('opd', 'OPD / Appointments (view)'),
        ('appointments', 'Book Appointments'),
        ('doctors', 'Doctors (roster)'),
        ('prescriptions', 'Prescriptions'),
        ('lab', 'Lab'),
        ('imaging', 'Imaging / Radiology'),
        ('ipd', 'IPD / Patient Admission'),
        ('ward', 'Ward / Nursing (medication & rounds)'),
        ('ot', 'OT / Surgery Management'),
    ]),
    ('Pharmacy', [
        ('pos', 'Point of Sale / Bills'),
        ('inventory', 'Inventory & Purchases'),
        ('customers', 'Customers'),
        ('suppliers', 'Suppliers'),
    ]),
    ('Finance', [
        ('billing', 'Billing / Invoices'),
        ('expenses', 'Expenses'),
        ('cashclosing', 'Cash Closing'),
        ('payouts', 'Doctor Payouts'),
        ('reports', 'Sales & Inventory Reports'),
        ('profit', 'Profit Report'),
        ('daybook', 'Day Book'),
    ]),
    ('System', [
        ('settings', 'Settings / Branding'),
        ('audit', 'Audit Log'),
    ]),
]

# keys the admin can toggle (everything shown in FEATURE_GROUPS)
EDITABLE_FEATURES = [k for _, items in FEATURE_GROUPS for k, _ in items]
FEATURE_LABELS = {k: label for _, items in FEATURE_GROUPS for k, label in items}


# ---------------------------------------------------------------------------
# Business-level MODULES (install-wide on/off, chosen at setup or in Settings).
# A module bundles feature keys. Core features are always on.
# ---------------------------------------------------------------------------
CORE_FEATURES = {'settings', 'audit', 'overview', 'catalog'}

MODULES = [
    ('pharmacy', 'Pharmacy', 'POS billing, inventory, purchases, customers & suppliers',
     ['pos', 'inventory', 'customers', 'suppliers']),
    ('opd', 'OPD / Hospital', 'Patients, doctors, appointments & prescriptions',
     ['patients', 'opd', 'appointments', 'doctors', 'prescriptions']),
    ('ipd', 'Inpatient (IPD)', 'Ward, bed, patient admission and daily rounds management',
     ['ipd', 'ward']),
    ('ot', 'Operation Theatre (OT)', 'Surgery booking, team scheduling and logs management',
     ['ot']),
    ('lab', 'Laboratory', 'Lab test orders & printed reports',
     ['lab']),
    ('imaging', 'Imaging / Radiology', 'Ultrasound, X-ray, CT, MRI studies & reports',
     ['imaging']),
    ('finance', 'Billing & Finance', 'Invoices, expenses, cash closing & doctor payouts',
     ['billing', 'expenses', 'cashclosing', 'payouts']),
    ('reports', 'Reports & Analytics', 'Sales, profit, inventory & day-book reports',
     ['reports', 'profit', 'daybook']),
]
MODULE_KEYS = [m[0] for m in MODULES]


def enabled_feature_set(enabled_modules):
    """Pure: given the list of enabled module keys (None/empty = ALL on), return
    the set of feature keys available at the install level."""
    if not enabled_modules:
        return set(FEATURES.keys())
    keys = set(CORE_FEATURES)
    for mkey, _label, _desc, fkeys in MODULES:
        if mkey in enabled_modules:
            keys.update(fkeys)
    return keys


def installed_features():
    """Feature keys turned on for THIS install (business module toggles)."""
    from saas.utils import get_current_hospital
    hospital = get_current_hospital()
    if hospital:
        return enabled_feature_set(hospital.enabled_modules)
    try:
        from user_mgmt.models import SiteSettings
        mods = SiteSettings.load().enabled_modules
    except Exception:
        mods = None
    return enabled_feature_set(mods)


def default_features_for_role(role):
    """Features a role gets out of the box."""
    return {k for k, roles in FEATURES.items() if role in roles}


def effective_features(user):
    """The features a user actually has: superuser = all; a user with an explicit
    `custom_features` list uses exactly that; otherwise the role defaults."""
    if getattr(user, 'is_superuser', False):
        return set(FEATURES.keys())
    custom = getattr(user, 'custom_features', None)
    if custom is not None:            # explicitly customised (even [])
        return {k for k in custom if k in FEATURES}
    return default_features_for_role(getattr(user, 'role', None))


def user_has_feature(user, key):
    if getattr(user, 'is_superuser', False):
        return True
    return key in effective_features(user)
