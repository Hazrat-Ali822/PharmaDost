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
    # Admitting, discharging, ward & bed setup. The paperwork half of IPD, so
    # reception is in. ACCOUNTANT is deliberately NOT: an accountant's interest in
    # an inpatient is the bill, which is `billing`, and holding `ipd` gave them the
    # whole ward — including a live Discharge button, which raises the bed-charge
    # invoice.
    'ipd':           {'ADMIN', 'RECEPTIONIST', 'DOCTOR'},
    # Bedside clinical work: medication administration, vitals, fluid balance,
    # nursing notes, care tasks, shift handover and doctor rounds.
    #
    # This is the CLINICAL-STAFF key, and that is why the doctor is in it. The
    # charting views used to accept `ipd` OR `ward`, so every receptionist could
    # record that a drug had been given and every accountant could chart a
    # patient's vitals — a record nobody in that job is qualified to write and
    # which the ward then reads as fact. They take `ward` alone now. An admin can
    # still grant it to an individual if a particular clinic works differently.
    'ward':          {'ADMIN', 'NURSE', 'DOCTOR'},
    # Ward In-charge / Charge Nurse: builds the duty roster and allocates the
    # ward's patients among the nurses on shift. A management capability — default
    # ADMIN only; the admin promotes a senior nurse to In-charge by granting this.
    'ward_manage':   {'ADMIN'},
    'ot':            {'ADMIN', 'DOCTOR'},
    # Emergency / Casualty — triage, casualty board, MLC. Front-line staff.
    'emergency':     {'ADMIN', 'DOCTOR', 'NURSE', 'RECEPTIONIST'},
    # Maternity / Obstetrics — ANC, deliveries, birth register.
    'maternity':     {'ADMIN', 'DOCTOR', 'NURSE'},
    # ICD-10 coded diagnoses.
    'diagnosis':     {'ADMIN', 'DOCTOR'},
    # Patient referrals in/out + printable referral letter.
    'referral':      {'ADMIN', 'DOCTOR', 'RECEPTIONIST'},
    # Birth & death certificates — the records office.
    'certificates':  {'ADMIN', 'DOCTOR', 'RECEPTIONIST'},
    # Blood bank — donors, unit inventory, issue.
    'bloodbank':     {'ADMIN', 'LABTECH', 'DOCTOR', 'NURSE'},
    # Vaccination / EPI records + immunization card.
    'vaccination':   {'ADMIN', 'DOCTOR', 'NURSE'},
    # Consent forms — template library + signed record.
    'consent':       {'ADMIN', 'DOCTOR', 'NURSE'},
    # Ambulance — dispatch board, fleet, drivers, trip log. Whoever answers the
    # phone books a run, so reception and the ward are both in.
    'ambulance':     {'ADMIN', 'RECEPTIONIST', 'NURSE', 'DOCTOR'},
    # Pharmacy
    'pos':           {'ADMIN', 'PHARMACIST', 'WHOLESALE'},
    'inventory':     {'ADMIN', 'PHARMACIST'},
    'customers':     {'ADMIN', 'PHARMACIST', 'WHOLESALE', 'ACCOUNTANT', 'RECEPTIONIST'},
    'suppliers':     {'ADMIN', 'PHARMACIST', 'ACCOUNTANT'},
    # Finance
    'billing':       {'ADMIN', 'RECEPTIONIST', 'ACCOUNTANT'},
    # Panels / Insurance / Sehat Card — setup & receivables (ADMIN/ACCOUNTANT),
    # and reception, who marks a patient as panel-covered at registration.
    'panel':         {'ADMIN', 'ACCOUNTANT', 'RECEPTIONIST'},
    'expenses':      {'ADMIN', 'ACCOUNTANT'},
    'cashclosing':   {'ADMIN', 'ACCOUNTANT'},
    'payouts':       {'ADMIN', 'ACCOUNTANT'},
    'reports':       {'ADMIN', 'PHARMACIST', 'ACCOUNTANT'},
    'profit':        {'ADMIN', 'ACCOUNTANT'},
    'daybook':       {'ADMIN', 'ACCOUNTANT'},
    # Staff HR — attendance, leave, payroll. Management + accounts.
    'hr':            {'ADMIN', 'ACCOUNTANT'},
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
        ('ward_manage', 'Ward In-charge (roster & patient allocation)'),
        ('ot', 'OT / Surgery Management'),
        ('emergency', 'Emergency / Casualty'),
        ('maternity', 'Maternity / Obstetrics'),
        ('diagnosis', 'Diagnoses (ICD-10)'),
        ('referral', 'Referrals (in / out)'),
        ('certificates', 'Birth & Death Certificates'),
        ('bloodbank', 'Blood Bank'),
        ('vaccination', 'Vaccination / EPI'),
        ('consent', 'Consent Forms'),
        ('ambulance', 'Ambulance / Dispatch'),
    ]),
    ('Pharmacy', [
        ('pos', 'Point of Sale / Bills'),
        ('inventory', 'Inventory & Purchases'),
        ('customers', 'Customers'),
        ('suppliers', 'Suppliers'),
    ]),
    ('Finance', [
        ('billing', 'Billing / Invoices'),
        ('panel', 'Panels / Insurance / Sehat Card'),
        ('expenses', 'Expenses'),
        ('cashclosing', 'Cash Closing'),
        ('payouts', 'Doctor Payouts'),
        ('reports', 'Sales & Inventory Reports'),
        ('profit', 'Profit Report'),
        ('daybook', 'Day Book'),
    ]),
    ('Staff', [
        ('hr', 'Staff HR (attendance, leave, payroll)'),
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
    ('opd', 'OPD / Hospital', 'Patients, doctors, appointments, prescriptions, diagnoses, referrals, certificates & consent',
     ['patients', 'opd', 'appointments', 'doctors', 'prescriptions', 'diagnosis',
      'referral', 'certificates', 'consent']),
    ('ipd', 'Inpatient (IPD)', 'Ward, bed, patient admission and daily rounds management',
     ['ipd', 'ward', 'ward_manage']),
    ('ot', 'Operation Theatre (OT)', 'Surgery booking, team scheduling and logs management',
     ['ot']),
    ('emergency', 'Emergency / Casualty', 'Triage board, casualty registration & medico-legal cases',
     ['emergency']),
    ('maternity', 'Maternity / Obstetrics', 'Antenatal care, deliveries & the birth register',
     ['maternity']),
    ('bloodbank', 'Blood Bank', 'Donor register, blood-unit inventory & issue to patients',
     ['bloodbank']),
    ('vaccination', 'Vaccination / EPI', 'EPI schedule, dose records & immunization card',
     ['vaccination']),
    ('ambulance', 'Ambulance', 'Fleet, drivers, dispatch board & trip charges',
     ['ambulance']),
    ('lab', 'Laboratory', 'Lab test orders & printed reports',
     ['lab']),
    ('imaging', 'Imaging / Radiology', 'Ultrasound, X-ray, CT, MRI studies & reports',
     ['imaging']),
    ('finance', 'Billing & Finance', 'Invoices, panels/insurance, expenses, cash closing & doctor payouts',
     ['billing', 'panel', 'expenses', 'cashclosing', 'payouts']),
    ('reports', 'Reports & Analytics', 'Sales, profit, inventory & day-book reports',
     ['reports', 'profit', 'daybook']),
    ('hr', 'Staff / HR', 'Staff profiles, attendance, leave & payroll',
     ['hr']),
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


def can_handle_prescriptions(user):
    """May this user open a patient's prescription?

    Three different jobs collide on one screen, so the rule cannot be a single
    feature key:

    * `prescriptions` is the **doctor's** key — write and edit an Rx.
    * The **pharmacist** does not hold it, but has to open the Rx they are
      dispensing from and mark a refused line declined. So `pos` counts too;
      that is why `prescription_detail` is gated on either.
    * The **wholesale operator** also holds `pos`, and must not count. That
      counter sells to other shops, has no patients at all, and a list of a
      named patient's prescribed medicines with their doctor is a medical
      record. A browser audit found `/prescriptions/<id>/` opening for them and
      the POS offering them a panel of six patients by name.

    Superusers pass, as everywhere else.
    """
    if getattr(user, 'is_superuser', False):
        return True
    if user_has_feature(user, 'prescriptions'):
        return True
    return (user_has_feature(user, 'pos')
            and getattr(user, 'role', None) != 'WHOLESALE')
