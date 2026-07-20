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


def notifications_context(request):
    """Fetch unread notifications for the logged-in user and pending task counters for the sidebar."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'unread_notifications': [],
            'unread_notifications_count': 0,
            'opd_badge_count': 0,
            'lab_badge_count': 0,
            'imaging_badge_count': 0,
            'ipd_badge_count': 0,
            'ot_badge_count': 0,
            'pharmacy_badge_count': 0,
            'admission_request_count': 0,
            'surgery_request_count': 0,
        }
    from .models import Notification
    unread = Notification.objects.filter(user=user, is_read=False).order_by('-created_at')[:5]
    unread_count = Notification.objects.filter(user=user, is_read=False).count()

    opd_badge_count = 0
    lab_badge_count = 0
    imaging_badge_count = 0
    ipd_badge_count = 0
    ot_badge_count = 0
    pharmacy_badge_count = 0
    admission_request_count = 0
    surgery_request_count = 0
    hospital = getattr(user, 'hospital', None)
    # Fail closed: every non-superuser's badge counts are scoped to their own
    # hospital (None → only hospital-less rows), never another tenant's.
    scope_by_hospital = not user.is_superuser

    try:
        # 1. OPD Queue count
        from opd.models import Appointment
        role = getattr(user, 'role', None)
        is_doctor = role == 'DOCTOR' and not user.is_superuser
        appt_qs = Appointment.objects.all()
        if scope_by_hospital:
            appt_qs = appt_qs.filter(patient__hospital=hospital)
        appt_qs = appt_qs.exclude(status__in=['DONE', 'CANCELLED'])
        if is_doctor:
            appt_qs = appt_qs.filter(doctor__user=user)
        opd_badge_count = appt_qs.count()

        # 2. Lab Pending count
        from lab.models import TestOrder
        lab_qs = TestOrder.objects.filter(status='Pending')
        if scope_by_hospital:
            lab_qs = lab_qs.filter(patient__hospital=hospital)
        if is_doctor:
            lab_qs = lab_qs.filter(ordered_by=user)
        lab_badge_count = lab_qs.count()

        # 3. Imaging Pending count
        from imaging.models import ImagingStudy
        img_qs = ImagingStudy.objects.filter(status='Pending')
        if scope_by_hospital:
            img_qs = img_qs.filter(patient__hospital=hospital)
        if is_doctor:
            img_qs = img_qs.filter(referred_by=user)
        imaging_badge_count = img_qs.count()

        # 4. IPD Active Admissions count
        from ipd.models import Admission
        ipd_qs = Admission.objects.filter(status='Admitted')
        if scope_by_hospital:
            ipd_qs = ipd_qs.filter(hospital=hospital)
        ipd_badge_count = ipd_qs.count()

        # 5. OT Surgeries in progress count
        from ot.models import SurgeryRecord
        ot_qs = SurgeryRecord.objects.filter(end_time__isnull=True)
        if scope_by_hospital:
            ot_qs = ot_qs.filter(hospital=hospital)
        ot_badge_count = ot_qs.count()

        # 6. Pharmacy Pending Prescription count
        from prescriptions.models import Prescription
        rx_qs = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL'])
        if scope_by_hospital:
            rx_qs = rx_qs.filter(appointment__patient__hospital=hospital)
        pharmacy_badge_count = rx_qs.count()

        # 7. Pending admission requests (doctor -> reception queue)
        from ipd.models import AdmissionRequest
        ar_qs = AdmissionRequest.objects.filter(status='Pending')
        if scope_by_hospital:
            ar_qs = ar_qs.filter(hospital=hospital)
        admission_request_count = ar_qs.count()

        # 8. Pending surgery requests (doctor -> OT queue)
        from ot.models import SurgeryRequest
        sr_qs = SurgeryRequest.objects.filter(status='Pending')
        if scope_by_hospital:
            sr_qs = sr_qs.filter(hospital=hospital)
        surgery_request_count = sr_qs.count()
    except Exception:
        pass

    return {
        'unread_notifications': unread,
        'unread_notifications_count': unread_count,
        'opd_badge_count': opd_badge_count,
        'lab_badge_count': lab_badge_count,
        'imaging_badge_count': imaging_badge_count,
        'ipd_badge_count': ipd_badge_count,
        'ot_badge_count': ot_badge_count,
        'pharmacy_badge_count': pharmacy_badge_count,
        'admission_request_count': admission_request_count,
        'surgery_request_count': surgery_request_count,
    }
