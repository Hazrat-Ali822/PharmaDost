import io
import json
import zipfile
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from accounts.decorators import role_required, feature_required
from accounts.models import User
from accounts.permissions import (FEATURE_GROUPS, EDITABLE_FEATURES,
                                  default_features_for_role, MODULES, MODULE_KEYS)
from .models import SiteSettings
from .site_forms import SiteSettingsForm
from .user_forms import UserForm


# Each role gets its OWN dashboard template.
ROLE_TEMPLATES = {
    'ADMIN': 'user_mgmt/dashboards/admin.html',
    'RECEPTIONIST': 'user_mgmt/dashboards/receptionist.html',
    'DOCTOR': 'user_mgmt/dashboards/doctor.html',
    'NURSE': 'user_mgmt/dashboards/nurse.html',
    'PHARMACIST': 'user_mgmt/dashboards/pharmacist.html',
    'WHOLESALE': 'user_mgmt/dashboards/wholesale.html',
    'LABTECH': 'user_mgmt/dashboards/labtech.html',
    'SONOGRAPHER': 'user_mgmt/dashboards/sonographer.html',
    'ACCOUNTANT': 'user_mgmt/dashboards/accountant.html',
}


def _template_for(user):
    role = getattr(user, 'role', None)
    template = ROLE_TEMPLATES.get(role)
    if not template and user.is_superuser:
        template = ROLE_TEMPLATES['ADMIN']
    return template or 'user_mgmt/dashboard_unknown.html'


@login_required
def dashboard_router(request):
    desktop = getattr(settings, 'DESKTOP_BUILD', False)
    # On the hosted site a hospital-less superuser is the SaaS owner → owner portal.
    # On the desktop/LAN build that same account IS the clinic admin, so it stays
    # here and lands on the clinic dashboard instead.
    if (request.user.is_superuser and not getattr(request.user, 'hospital', None)
            and not desktop):
        return redirect('saas:dashboard')
    if getattr(request.user, 'role', None) == 'ADMIN' or (desktop and request.user.is_superuser):
        # Admins land on the pharmacy dashboard — but only if pharmacy is on and
        # they actually have inventory access; otherwise show the admin shell
        # directly (avoids a redirect loop with the "/" dashboard guard).
        from accounts.permissions import user_has_feature, installed_features
        if 'inventory' in installed_features() and user_has_feature(request.user, 'inventory'):
            return redirect('dashboard')
        from . import overview
        ctx = overview.build(request.user)
        ctx['role'] = 'ADMIN'
        return render(request, _template_for(request.user), ctx)

    import datetime
    from django.utils import timezone
    from django.db.models import Sum

    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    try:
        if start_date_str:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        else:
            start_date = today - datetime.timedelta(days=30)
    except ValueError:
        start_date = today - datetime.timedelta(days=30)

    try:
        if end_date_str:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            end_date = today
    except ValueError:
        end_date = today

    ctx = {
        'role': getattr(request.user, 'role', None),
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
    }

    hospital = getattr(request.user, 'hospital', None)
    # Fail closed: a non-superuser's dashboard is scoped to their own hospital
    # (None → only hospital-less rows), never another tenant's.
    scope_by_hospital = not request.user.is_superuser

    if ctx['role'] == 'SONOGRAPHER':
        from imaging.models import ImagingStudy
        studies = ImagingStudy.objects.all()
        if scope_by_hospital:
            studies = studies.filter(patient__hospital=hospital)
        pending = studies.exclude(status__in=['Reported', 'Delivered'])
        ctx['pending_studies'] = pending.order_by('study_date')[:15]
        ctx['pending_count'] = pending.count()
        ctx['reported_count'] = studies.filter(status__in=['Reported', 'Delivered'], study_date__date__range=[start_date, end_date]).count()
        ctx['revenue_collected'] = studies.filter(payment_collected_by=request.user, payment_status='Paid', study_date__date__range=[start_date, end_date]).aggregate(s=Sum('payment_amount'))['s'] or 0
    elif ctx['role'] == 'LABTECH':
        from lab.models import TestOrder
        orders = TestOrder.objects.select_related('patient').prefetch_related('results')
        if scope_by_hospital:
            orders = orders.filter(patient__hospital=hospital)
        done = ['Completed', 'Verified', 'Delivered']
        pending = orders.exclude(status__in=done)
        ctx['pending_orders'] = pending.order_by('order_date')[:15]
        ctx['pending_count'] = pending.count()
        ctx['completed_count'] = orders.filter(status__in=done, order_date__date__range=[start_date, end_date]).count()
        ctx['revenue_collected'] = orders.filter(payment_collected_by=request.user, payment_status='Paid', order_date__date__range=[start_date, end_date]).aggregate(s=Sum('payment_amount'))['s'] or 0
    elif ctx['role'] == 'DOCTOR':
        from opd.models import Appointment
        from billing.models import Invoice
        appts = Appointment.objects.filter(doctor__user=request.user, appointment_date__range=[start_date, end_date], status='DONE')
        ctx['patient_count'] = appts.count()
        ctx['revenue_collected'] = Invoice.objects.filter(appointment__in=appts).aggregate(s=Sum('total'))['s'] or 0
    elif ctx['role'] == 'NURSE':
        from ipd.models import Admission
        admissions = Admission.objects.filter(status='Admitted').select_related('patient', 'bed__ward', 'attending_doctor')
        if scope_by_hospital:
            admissions = admissions.filter(patient__hospital=hospital)
        ctx['admissions'] = admissions.order_by('bed__ward__name', 'bed__bed_number')
        ctx['admitted_count'] = admissions.count()
    elif ctx['role'] == 'PHARMACIST':
        from sales.models import Sale
        from prescriptions.models import Prescription
        sales = Sale.objects.filter(cashier=request.user, created_at__date__range=[start_date, end_date], is_returned=False)
        ctx['sales_count'] = sales.count()
        ctx['revenue_collected'] = sales.aggregate(s=Sum('paid'))['s'] or 0
        pending_rx = Prescription.objects.filter(status__in=['PENDING', 'PARTIAL']).select_related('appointment__patient', 'appointment__doctor__user').prefetch_related('items__medicine')
        if scope_by_hospital:
            pending_rx = pending_rx.filter(appointment__patient__hospital=hospital)
        ctx['pending_prescriptions'] = pending_rx.order_by('-created_at')[:20]

    return render(request, _template_for(request.user), ctx)


# Legacy dashboard routes, kept so old links and `{% url 'user_mgmt:...' %}`
# references still resolve. They were `@login_required` and nothing else, which
# made every one of them a way to render somebody else's dashboard:
#
#   * `/manage/dashboard/admin/` handed ANY signed-in user — a nurse, a wholesale
#     operator — the full owner overview: the day's revenue and what is unpaid,
#     the attention list, the OPD board, and the recent audit feed (who signed in
#     and when). The audit trail is the most sensitive page in the product and is
#     tenant-scoped for exactly that reason; this route re-exposed it to every
#     role inside the tenant.
#   * The other four rendered a named role's dashboard to whoever asked.
#
# So the admin one is role-gated, and the rest simply route the caller to their
# own dashboard instead of rendering a fixed one.

@role_required(['ADMIN'])
def admin_dashboard(request):
    from . import overview
    return render(request, ROLE_TEMPLATES['ADMIN'], overview.build(request.user))


@login_required
def manager_dashboard(request):
    return redirect('user_mgmt:post_login_redirect')


@login_required
def pharmacist_dashboard(request):
    return redirect('user_mgmt:post_login_redirect')


@login_required
def lab_dashboard(request):
    return redirect('user_mgmt:post_login_redirect')


@login_required
def sonographer_dashboard(request):
    return redirect('user_mgmt:post_login_redirect')


# ---------------------------------------------------------------- user guide / help

# For each role, the guide sections that matter most to it (anchors in
# templates/help/guide.html). Admin sees everything. Everyone can still scroll to
# any section — this only powers the "Start here for your role" quick links.
ROLE_GUIDE_SECTIONS = {
    'ADMIN': ['start', 'reception', 'doctor', 'emergency', 'maternity', 'diagnosis',
              'referral', 'pharmacy', 'lab', 'imaging', 'bloodbank', 'vaccination',
              'ipd', 'ot', 'billing', 'panels', 'reports', 'hr', 'certificates',
              'consent', 'users', 'settings', 'offline'],
    'RECEPTIONIST': ['start', 'reception', 'emergency', 'referral', 'billing',
                     'panels', 'certificates', 'offline'],
    'DOCTOR': ['start', 'doctor', 'emergency', 'maternity', 'diagnosis', 'referral',
               'bloodbank', 'vaccination', 'ipd', 'ot', 'consent', 'certificates',
               'offline'],
    'NURSE': ['start', 'emergency', 'maternity', 'bloodbank', 'vaccination', 'ipd',
              'consent', 'offline'],
    'PHARMACIST': ['start', 'pharmacy', 'offline'],
    'WHOLESALE': ['start', 'pharmacy', 'offline'],
    'LABTECH': ['start', 'lab', 'bloodbank', 'offline'],
    'SONOGRAPHER': ['start', 'imaging', 'offline'],
    'ACCOUNTANT': ['start', 'billing', 'panels', 'reports', 'hr', 'offline'],
}

# Human labels for the anchors, so the role card can name them.
GUIDE_SECTION_LABELS = {
    'start': 'Getting started', 'reception': 'Front desk / Reception',
    'doctor': 'Doctor / OPD',
    'emergency': 'Emergency / Casualty', 'maternity': 'Maternity / ANC',
    'diagnosis': 'Diagnoses (ICD-10)', 'referral': 'Referrals',
    'pharmacy': 'Pharmacy', 'lab': 'Laboratory',
    'imaging': 'Imaging / Radiology',
    'bloodbank': 'Blood Bank', 'vaccination': 'Vaccination / EPI',
    'ipd': 'Inpatient & Ward (IPD)', 'ot': 'Operation Theatre',
    'billing': 'Billing & Finance', 'panels': 'Panels / Insurance',
    'reports': 'Reports', 'hr': 'Staff HR',
    'certificates': 'Certificates', 'consent': 'Consent Forms',
    'users': 'Users & Access', 'settings': 'Settings & Branding',
    'offline': 'Working offline',
}


@login_required
def help_center(request):
    role = getattr(request.user, 'role', '') or ''
    if request.user.is_superuser:
        role = 'ADMIN'
    keys = ROLE_GUIDE_SECTIONS.get(role, ['start', 'offline'])
    my_sections = [(k, GUIDE_SECTION_LABELS[k]) for k in keys]
    return render(request, 'help/guide.html', {
        'role': role,
        'role_label': dict(getattr(User, 'ROLE_CHOICES', [])).get(role, role.title() or 'Staff'),
        'my_sections': my_sections,
    })


# ---------------------------------------------------------------- first-run setup
def setup_wizard(request):
    """Shown on a fresh install (no users yet): create the admin account, name
    the business, and choose which modules to enable."""
    if User.objects.exists():
        return redirect('/login/')  # already configured

    error = None
    selected = MODULE_KEYS  # pre-check everything
    if request.method == 'POST':
        brand = request.POST.get('brand_name', '').strip() or 'Sehatyar'
        tagline = request.POST.get('brand_tagline', '').strip()
        email = request.POST.get('email', '').strip().lower()
        pwd = request.POST.get('password', '')
        selected = [m for m in request.POST.getlist('modules') if m in MODULE_KEYS]
        if not email or not pwd:
            error = 'Admin email and password are required.'
        elif len(pwd) < 6:
            error = 'Password must be at least 6 characters.'
        elif not selected:
            error = 'Please choose at least one module.'
        else:
            User.objects.create_superuser(email=email, password=pwd, role='ADMIN')
            s = SiteSettings.load()
            s.brand_name = brand
            if tagline:
                s.brand_tagline = tagline
            s.enabled_modules = selected
            s.save()
            messages.success(request, 'Setup complete! Please sign in with your admin account.')
            return redirect('/login/')

    return render(request, 'user_mgmt/setup.html',
                  {'modules': MODULES, 'error': error, 'selected': selected})


# ---------------------------------------------------------------- user management
def _role_defaults_json():
    """role -> sorted list of default feature keys, for the access-editor JS."""
    roles = [r for r, _ in User.ROLE_CHOICES]
    return json.dumps({r: sorted(default_features_for_role(r)) for r in roles})


def _apply_features(request, user):
    """Set custom_features from the form: None (inherit role) unless 'customize'
    is ticked, in which case store exactly the ticked features."""
    if request.POST.get('customize'):
        chosen = [f for f in request.POST.getlist('features') if f in EDITABLE_FEATURES]
        user.custom_features = chosen
    else:
        user.custom_features = None


def _user_form_ctx(request, form, user=None):
    if user is not None and user.custom_features is not None:
        selected = set(user.custom_features)
        customize = True
    elif user is not None:
        selected = user.effective_features()
        customize = False
    else:
        selected = set()
        customize = False
    return {
        'form': form,
        'groups': FEATURE_GROUPS,
        'selected': selected,
        'customize': customize,
        'role_defaults_json': _role_defaults_json(),
        'edit_user': user,
    }


@role_required(["ADMIN"])
def user_list(request):
    if request.user.is_superuser and not getattr(request.user, 'hospital', None):
        users = User.objects.all().order_by('email')
    else:
        users = User.objects.filter(hospital=request.user.hospital).order_by('email')
    return render(request, 'user_mgmt/user_list.html', {'users': users})


@role_required(["ADMIN"])
def user_create(request):
    if request.method == 'POST':
        form = UserForm(request.POST)
        if form.is_valid():
            pwd = form.cleaned_data.get('password')
            if not pwd:
                form.add_error('password', 'Password is required for a new user.')
            else:
                user = form.save(commit=False)
                user.set_password(pwd)
                if not request.user.is_superuser or getattr(request.user, 'hospital', None):
                    user.hospital = request.user.hospital
                _apply_features(request, user)
                user.save()
                messages.success(request, f'User {user.email} created.')
                return redirect('user_mgmt:user_list')
    else:
        form = UserForm()
    ctx = _user_form_ctx(request, form)
    ctx['title'] = 'Add User'
    return render(request, 'user_mgmt/user_form.html', ctx)


@role_required(["ADMIN"])
def user_edit(request, pk):
    if request.user.is_superuser and not getattr(request.user, 'hospital', None):
        user = get_object_or_404(User, pk=pk)
    else:
        user = get_object_or_404(User, pk=pk, hospital=request.user.hospital)
    if request.method == 'POST':
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            u = form.save(commit=False)
            pwd = form.cleaned_data.get('password')
            if pwd:
                u.set_password(pwd)
            _apply_features(request, u)
            u.save()
            messages.success(request, f'User {u.email} updated.')
            return redirect('user_mgmt:user_list')
    else:
        form = UserForm(instance=user)
    ctx = _user_form_ctx(request, form, user=user)
    ctx['title'] = f'Edit {user.email}'
    return render(request, 'user_mgmt/user_form.html', ctx)


@feature_required('settings')
def site_settings(request):
    site = SiteSettings.load()
    # The public demo signs every visitor in as an ADMIN, so without this any
    # passer-by could rename the hospital, upload their own logo or switch modules
    # off — for everyone who visits afterwards. The screen still opens (it is part
    # of what the demo is showing); only saving is refused.
    from saas.utils import is_demo_hospital
    demo_locked = is_demo_hospital(getattr(request.user, 'hospital', None))
    if request.method == 'POST':
        if demo_locked:
            messages.warning(
                request, 'This is the public demo — settings cannot be changed here. '
                         'Everything else is fully editable.')
            return redirect('user_mgmt:site_settings')
        if 'reset' in request.POST:
            site.reset_to_defaults()
            messages.success(request, 'Branding reset to defaults.')
            return redirect('user_mgmt:site_settings')
        form = SiteSettingsForm(request.POST, request.FILES, instance=site)
        if form.is_valid():
            obj = form.save(commit=False)
            mods = [m for m in request.POST.getlist('modules') if m in MODULE_KEYS]
            obj.enabled_modules = mods if mods else None
            # Pull the theme colour straight out of the logo when asked — one tick
            # themes the whole app to match the hospital's own logo.
            if request.POST.get('color_from_logo'):
                from .color_utils import dominant_color, darker
                src = request.FILES.get('logo_image') or (obj.logo_image or None)
                color = dominant_color(src) if src else None
                if color:
                    obj.primary_color = color
                    obj.accent_color = darker(color)
                    messages.info(request, f'Theme colour picked from the logo: {color}.')
                else:
                    messages.warning(request, 'Could not read a colour from the logo — upload a clearer logo, or set the colour by hand.')
            obj.save()
            messages.success(request, 'Settings saved.')
            return redirect('user_mgmt:site_settings')
    else:
        form = SiteSettingsForm(instance=site)
    enabled = site.enabled_modules if site.enabled_modules is not None else MODULE_KEYS
    return render(request, 'user_mgmt/site_settings.html',
                  {'form': form, 'site': site, 'modules': MODULES, 'enabled': enabled,
                   'demo_locked': demo_locked})


def can_download_raw_backup(user):
    """Who may pull the raw database + media archive.

    **The archive is the whole install, not one hospital.** On the hosted site
    that is a single SQLite file holding every tenant — patients, bills, staff,
    password hashes — and `MEDIA_ROOT` likewise holds every tenant's uploads. So
    this is only ever safe for someone who owns the whole install:

      * the **desktop / LAN build**, where the clinic *is* the install and the
        admin owns their own data (this is what the button was written for), and
      * a **superuser** on the hosted site, i.e. the SaaS owner.

    It shipped as `@role_required(["ADMIN"])` alone, which put a one-click
    download of every customer's database in each customer's own topbar.
    """
    if getattr(user, 'is_superuser', False):
        return True
    return bool(getattr(settings, 'DESKTOP_BUILD', False))


@role_required(["ADMIN"])
def backup_download(request):
    """One-click backup: zip up the whole database + uploaded media and send it as a
    download. Restoring is just unzipping these two into the data folder. Handy for the
    local desktop app where the admin owns their own data.

    Gated by `can_download_raw_backup` — see there for why a tenant admin on the
    hosted site must not have this.
    """
    if not can_download_raw_backup(request.user):
        return HttpResponseForbidden(
            "This download contains the whole system's database, not just your "
            "hospital's records, so it is limited to the system owner. Ask your "
            "provider for a copy of your hospital's data."
        )
    db_path = Path(settings.DATABASES['default']['NAME'])
    media_root = Path(settings.MEDIA_ROOT)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        if db_path.exists():
            z.write(db_path, 'db.sqlite3')
        if media_root.exists():
            for f in media_root.rglob('*'):
                if f.is_file():
                    z.write(f, Path('media') / f.relative_to(media_root))
    buf.seek(0)

    from django.utils import timezone
    name = f"sehatyar-backup-{timezone.localtime().strftime('%Y%m%d-%H%M')}.zip"
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{name}"'
    return resp


@role_required(["ADMIN"])
def license_manage(request):
    """Settings → Licence: show the current subscription state of this desktop /
    LAN install and let an admin paste the monthly key the provider sends.

    Only meaningful on the desktop build (``DESKTOP_BUILD``); on the hosted site
    subscription is handled by the SaaS portal instead, so the page says so.
    """
    from user_mgmt import licensing as core

    if request.method == 'POST':
        token = (request.POST.get('token') or '').strip()
        if core.save_license(settings.DATA_DIR, token):
            messages.success(request, 'Licence activated. Thank you.')
        else:
            messages.error(request, 'That licence key is not valid — check you '
                                    'copied the whole thing, with no spaces.')
        return redirect('user_mgmt:license')

    state = core.license_state(settings.DATA_DIR)
    return render(request, 'user_mgmt/license.html',
                  {'license': state, 'desktop_build': settings.DESKTOP_BUILD})


def _safe_backup_members(zf):
    """Reject a zip that tries to escape the data folder (zip-slip): no absolute
    paths, no '..'. Returns the member names, or raises ValueError."""
    for name in zf.namelist():
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or (len(name) > 1 and name[1] == ':'):
            raise ValueError(f"unsafe path in archive: {name}")
    return zf.namelist()


@role_required(["ADMIN"])
def restore_upload(request):
    """Restore the whole install from a backup zip (the reverse of the Backup
    button) — for putting a clinic's data back on a fresh computer after loss.

    Desktop build only: a running SQLite DB cannot be swapped mid-flight, so this
    only **stages** the upload and drops a marker; `desktop.launcher.apply_pending_
    restore` does the swap on the next launch. The user is told to restart.
    """
    import shutil
    import zipfile

    if not settings.DESKTOP_BUILD:
        messages.error(request, "Restore is only available in the desktop app.")
        return redirect('user_mgmt:site_settings')

    if request.method == 'POST':
        f = request.FILES.get('backup')
        if not f:
            messages.error(request, "Choose a backup .zip file first.")
            return redirect('user_mgmt:restore')

        staging = Path(settings.DATA_DIR) / "_restore_pending"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(f) as z:
                names = _safe_backup_members(z)
                if 'db.sqlite3' not in names:
                    raise ValueError("this is not a Sehatyar backup (no db.sqlite3)")
                z.extractall(staging)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            messages.error(request, f"That backup could not be read: {exc}")
            return redirect('user_mgmt:restore')

        (Path(settings.DATA_DIR) / "RESTORE_PENDING").write_text("1", encoding="utf-8")
        messages.success(
            request, "Backup loaded. Now CLOSE the app completely and open it again — "
                     "the data is put back on restart. (Nothing is lost until you do.)")
        return redirect('user_mgmt:restore')

    return render(request, 'user_mgmt/restore.html',
                  {'desktop_build': settings.DESKTOP_BUILD})
