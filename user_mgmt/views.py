import io
import json
import zipfile
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
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
    ctx = {'role': getattr(request.user, 'role', None)}
    if ctx['role'] == 'SONOGRAPHER':
        from imaging.models import ImagingStudy
        studies = ImagingStudy.objects.select_related('patient')
        pending = studies.exclude(status__in=['Reported', 'Delivered'])
        ctx['pending_studies'] = pending.order_by('study_date')[:15]
        ctx['pending_count'] = pending.count()
        ctx['reported_count'] = studies.filter(status__in=['Reported', 'Delivered']).count()
    elif ctx['role'] == 'LABTECH':
        from lab.models import TestOrder
        orders = TestOrder.objects.select_related('patient').prefetch_related('results')
        done = ['Completed', 'Verified', 'Delivered']
        pending = orders.exclude(status__in=done)
        ctx['pending_orders'] = pending.order_by('order_date')[:15]
        ctx['pending_count'] = pending.count()
        ctx['completed_count'] = orders.filter(status__in=done).count()
    return render(request, _template_for(request.user), ctx)


# Kept so existing {% url 'user_mgmt:...' %} references and direct links still resolve.
@login_required
def admin_dashboard(request):
    return render(request, ROLE_TEMPLATES['ADMIN'])


@login_required
def manager_dashboard(request):
    # legacy name — route by the caller's role
    return render(request, _template_for(request.user))


@login_required
def pharmacist_dashboard(request):
    return render(request, ROLE_TEMPLATES['PHARMACIST'])


@login_required
def lab_dashboard(request):
    return render(request, ROLE_TEMPLATES['LABTECH'])


@login_required
def sonographer_dashboard(request):
    return render(request, ROLE_TEMPLATES['SONOGRAPHER'])


# ---------------------------------------------------------------- first-run setup
def setup_wizard(request):
    """Shown on a fresh install (no users yet): create the admin account, name
    the business, and choose which modules to enable."""
    if User.objects.exists():
        return redirect('/login/')  # already configured

    error = None
    selected = MODULE_KEYS  # pre-check everything
    if request.method == 'POST':
        brand = request.POST.get('brand_name', '').strip() or 'PharmaDost'
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
    users = User.objects.order_by('email')
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
    user = get_object_or_404(User, pk=pk)
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
    if request.method == 'POST':
        if 'reset' in request.POST:
            site.reset_to_defaults()
            messages.success(request, 'Branding reset to defaults.')
            return redirect('user_mgmt:site_settings')
        form = SiteSettingsForm(request.POST, request.FILES, instance=site)
        if form.is_valid():
            obj = form.save(commit=False)
            mods = [m for m in request.POST.getlist('modules') if m in MODULE_KEYS]
            obj.enabled_modules = mods if mods else None
            obj.save()
            messages.success(request, 'Settings saved.')
            return redirect('user_mgmt:site_settings')
    else:
        form = SiteSettingsForm(instance=site)
    enabled = site.enabled_modules if site.enabled_modules is not None else MODULE_KEYS
    return render(request, 'user_mgmt/site_settings.html',
                  {'form': form, 'site': site, 'modules': MODULES, 'enabled': enabled})


@role_required(["ADMIN"])
def backup_download(request):
    """One-click backup: zip up the whole database + uploaded media and send it as a
    download. Restoring is just unzipping these two into the data folder. Handy for the
    local desktop app where the admin owns their own data."""
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
    name = f"pharmadost-backup-{timezone.localtime().strftime('%Y%m%d-%H%M')}.zip"
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{name}"'
    return resp
