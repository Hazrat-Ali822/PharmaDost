import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.db import models
from django.utils.text import slugify
from .models import Hospital, HospitalPayment, PlatformExpense, DesktopBackup
from accounts.models import User
from accounts.permissions import MODULES

# Decorator to ensure only superuser can access SaaS management
def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url='/login/')(view_func)

def _by_hospital(qs, value):
    """One grouped query -> {hospital_id: aggregate}. Keeps per-tenant stats flat
    (a fixed handful of queries) instead of one query per hospital in a loop."""
    return {row['hospital_id']: row['v']
            for row in qs.values('hospital_id').annotate(v=value)}


@superuser_required
def saas_dashboard(request):
    from patients.models import Patient
    from sales.models import Sale

    today = timezone.localdate()
    month_start = today.replace(day=1)

    hospitals = list(Hospital.objects.all().order_by('-created_at'))

    # Per-tenant intelligence: each of these is a SINGLE grouped query over all
    # tenants, built into a dict and attached in Python — no query in the loop.
    users_by = _by_hospital(User.objects.all(), models.Count('id'))
    patients_by = _by_hospital(Patient.objects.all(), models.Count('id'))
    sales_by = _by_hospital(Sale.objects.all(), models.Count('id'))
    revenue_by = _by_hospital(
        Sale.objects.filter(created_at__date__gte=month_start), models.Sum('total'))
    last_sale_by = _by_hospital(Sale.objects.all(), models.Max('created_at'))

    for h in hospitals:
        h.n_users = users_by.get(h.id, 0)
        h.n_patients = patients_by.get(h.id, 0)
        h.n_sales = sales_by.get(h.id, 0)
        h.rev_month = revenue_by.get(h.id) or 0
        h.last_sale = last_sale_by.get(h.id)
        h.days_left = (h.expiry_date - today).days
        if not h.is_active:
            h.status = 'blocked'
        elif h.days_left < 0:
            h.status = 'expired'
        elif h.days_left <= 7:
            h.status = 'expiring'
        else:
            h.status = 'active'

    payments = (HospitalPayment.objects.select_related('hospital')
                .order_by('-payment_date')[:10])
    expenses = PlatformExpense.objects.all().order_by('-expense_date')[:10]

    # Platform-wide metrics
    active_count = sum(1 for h in hospitals if h.status == 'active')
    expiring_count = sum(1 for h in hospitals if h.status == 'expiring')
    expired_count = sum(1 for h in hospitals if h.status == 'expired')
    blocked_count = sum(1 for h in hospitals if h.status == 'blocked')
    attention_count = expiring_count + expired_count + blocked_count

    projected_income = sum((h.monthly_price for h in hospitals if h.is_active), 0)
    total_received = HospitalPayment.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    total_expense = PlatformExpense.objects.aggregate(total=models.Sum('amount'))['total'] or 0
    net_profit = total_received - total_expense

    from prescriptions.models import Prescription
    from ipd.models import Admission
    from lab.models import TestOrder

    total_prescriptions = Prescription.objects.count()
    total_admissions = Admission.objects.count()
    total_lab_orders = TestOrder.objects.count()

    context = {
        'hospitals': hospitals,
        'payments': payments,
        'expenses': expenses,
        'active_count': active_count,
        'expiring_count': expiring_count,
        'expired_count': expired_count,
        'blocked_count': blocked_count,
        'hospital_count': len(hospitals),
        'attention_count': attention_count,
        'total_patients': sum(patients_by.values()),
        'total_sales': sum(sales_by.values()),
        'total_users': sum(users_by.values()),
        'total_prescriptions': total_prescriptions,
        'total_admissions': total_admissions,
        'total_lab_orders': total_lab_orders,
        'projected_income': projected_income,
        'total_received': total_received,
        'total_expense': total_expense,
        'net_profit': net_profit,
    }
    return render(request, 'saas/dashboard.html', context)

@superuser_required
def hospital_create(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        slug = request.POST.get('slug')
        monthly_price = request.POST.get('monthly_price')
        expiry_date = request.POST.get('expiry_date')
        is_active = request.POST.get('is_active') == 'on'
        selected_modules = request.POST.getlist('modules')

        admin_email = request.POST.get('admin_email')
        admin_password = request.POST.get('admin_password')

        if not slug:
            slug = slugify(name)

        if Hospital.objects.filter(slug=slug).exists():
            messages.error(request, f"A hospital with slug '{slug}' already exists.")
            return render(request, 'saas/hospital_form.html', {'modules': MODULES})

        if User.objects.filter(email=admin_email).exists():
            messages.error(request, f"User with email '{admin_email}' already exists.")
            return render(request, 'saas/hospital_form.html', {'modules': MODULES})

        # 1. Create Hospital
        hospital = Hospital.objects.create(
            name=name,
            slug=slug,
            monthly_price=monthly_price,
            expiry_date=expiry_date,
            is_active=is_active,
            enabled_modules=selected_modules
        )

        # 2. Create Hospital Admin Account
        admin_user = User.objects.create_user(email=admin_email, password=admin_password)
        admin_user.role = 'ADMIN'
        admin_user.hospital = hospital
        admin_user.save()

        # 3. Seed Comprehensive Lab & Radiology Catalogs
        from saas.catalog_seeder import seed_hospital_catalogs
        seed_hospital_catalogs(hospital)

        messages.success(request, f"Hospital '{name}' and Admin account '{admin_email}' created successfully with diagnostic catalogs!")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_form.html', {'modules': MODULES})

@superuser_required
def hospital_edit(request, pk):
    hospital = get_object_or_404(Hospital, pk=pk)
    if request.method == 'POST':
        hospital.name = request.POST.get('name')
        hospital.monthly_price = request.POST.get('monthly_price')
        hospital.expiry_date = request.POST.get('expiry_date')
        hospital.is_active = request.POST.get('is_active') == 'on'
        hospital.enabled_modules = request.POST.getlist('modules')
        hospital.save()

        # Sync diagnostic catalogs based on current enabled modules
        from saas.catalog_seeder import seed_hospital_catalogs
        seed_hospital_catalogs(hospital)

        messages.success(request, f"Hospital '{hospital.name}' updated successfully!")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_form.html', {'hospital': hospital, 'modules': MODULES})

@superuser_required
def hospital_delete(request, pk):
    """Permanently delete a hospital and EVERYTHING under it. Superuser-only,
    and guarded by a type-the-name confirmation because it cannot be undone.

    `purge_tenant` wipes every hospital-scoped row first, in dependency-safe
    passes — a bare `hospital.delete()` cannot, because child rows hold PROTECT
    FKs to their parents (SaleItem→Medicine, Invoice→Patient, Sale→Customer, …)
    and the cascade raises `ProtectedError` on any tenant that has actually
    traded. `User.hospital` is SET_NULL, so staff would otherwise linger as
    orphaned accounts that can still sign in: we capture their ids first and
    delete them once everything that referenced them is gone."""
    from django.db import transaction
    from patients.models import Patient
    from sales.models import Sale
    from billing.models import Invoice

    hospital = get_object_or_404(Hospital, pk=pk)
    stats = {
        'patients': Patient.objects.filter(hospital=hospital).count(),
        'staff': User.objects.filter(hospital=hospital, is_superuser=False).count(),
        'sales': Sale.objects.filter(hospital=hospital).count(),
        'invoices': Invoice.objects.filter(hospital=hospital).count(),
    }

    if request.method == 'POST':
        typed = (request.POST.get('confirm_name') or '').strip()
        if typed != hospital.name:
            messages.error(request, "The name you typed does not match. Nothing was deleted.")
            return render(request, 'saas/hospital_confirm_delete.html',
                          {'hospital': hospital, 'stats': stats})

        name = hospital.name
        # Capture staff ids BEFORE the cascade nulls their hospital, and delete
        # them AFTER, when no tenant rows reference them any more.
        staff_ids = list(
            User.objects.filter(hospital=hospital, is_superuser=False)
            .values_list('id', flat=True))
        from audit.middleware import suppress_audit
        from saas.services import purge_tenant
        with transaction.atomic(), suppress_audit():
            purge_tenant(hospital)
            hospital.delete()
            if staff_ids:
                User.objects.filter(id__in=staff_ids).delete()

        messages.success(
            request,
            f"Hospital '{name}' and all of its data ({stats['patients']} patients, "
            f"{stats['staff']} staff, {stats['sales']} bills) were permanently deleted.")
        return redirect('saas:dashboard')

    return render(request, 'saas/hospital_confirm_delete.html',
                  {'hospital': hospital, 'stats': stats})

@superuser_required
def payment_create(request):
    hospitals = Hospital.objects.all()
    if request.method == 'POST':
        hospital_id = request.POST.get('hospital_id')
        amount = request.POST.get('amount')
        payment_date = request.POST.get('payment_date')
        note = request.POST.get('note', '')

        hospital = get_object_or_404(Hospital, id=hospital_id)
        HospitalPayment.objects.create(
            hospital=hospital,
            amount=amount,
            payment_date=payment_date,
            note=note
        )
        messages.success(request, f"Recorded Rs {amount} payment for {hospital.name}!")
        return redirect('saas:dashboard')

    return render(request, 'saas/payment_form.html', {'hospitals': hospitals})

@superuser_required
def expense_create(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        amount = request.POST.get('amount')
        expense_date = request.POST.get('expense_date')
        note = request.POST.get('note', '')

        PlatformExpense.objects.create(
            title=title,
            amount=amount,
            expense_date=expense_date,
            note=note
        )
        messages.success(request, f"Recorded Rs {amount} platform expense for '{title}'!")
        return redirect('saas:dashboard')

    return render(request, 'saas/expense_form.html')


from django.contrib.auth import authenticate, login as auth_login
from django.utils import timezone


def _add_months(d, months):
    """Add whole months to a date, clamping the day to the target month's last day
    (31 Jan + 1 month -> 28/29 Feb). No dateutil dependency."""
    import calendar
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _hospital_detail_context(hospital, **extra):
    today = timezone.localdate()
    hospital.days_left = (hospital.expiry_date - today).days
    payments = hospital.payments.order_by('-payment_date', '-created_at')
    total_paid = payments.aggregate(t=models.Sum('amount'))['t'] or 0
    ctx = {'hospital': hospital, 'payments': payments,
           'total_paid': total_paid, 'today': today,
           'have_license_key': _load_license_private_key() is not None}
    ctx.update(extra)
    return ctx


@superuser_required
def hospital_detail(request, pk):
    """One tenant's subscription page: status + renewal/payment history + a one-click
    desktop/LAN licence generator bound to this tenant."""
    hospital = get_object_or_404(Hospital, pk=pk)
    return render(request, 'saas/hospital_detail.html',
                  _hospital_detail_context(hospital))


@superuser_required
def hospital_desktop_license(request, pk):
    """Generate a desktop/LAN licence key **for this tenant** — clinic name and slug
    taken from the hospital, so the key (and the backups it later uploads) are tied to
    it. Same signed key as `sign_license.py`, one click from the tenant's page."""
    from user_mgmt.licensing import make_token

    hospital = get_object_or_404(Hospital, pk=pk)
    if request.method != 'POST':
        return redirect('saas:hospital_detail', pk=pk)

    priv = _load_license_private_key()
    token = None
    try:
        months = max(1, min(int(request.POST.get('months') or 1), 60))
    except (TypeError, ValueError):
        months = 1
    if priv is None:
        messages.error(request, "No signing key on this server. Set "
                       "DESKTOP_LICENSE_PRIVATE_KEY or upload licensing/private_key.json.")
    else:
        today = timezone.localdate()
        extra = {'slug': hospital.slug}
        machine = (request.POST.get('machine') or '').strip()
        if machine:
            extra['machine'] = machine       # lock the key to that one computer
        token = make_token(hospital.name, _add_months(today, months), today, priv,
                           extra=extra)
    return render(request, 'saas/hospital_detail.html',
                  _hospital_detail_context(hospital, gen_token=token, gen_months=months))


@superuser_required
def hospital_renew(request, pk):
    """Extend a tenant's subscription by N months AND record the payment in one
    step, so the renewal history builds itself. Renewing early adds on top of the
    time left (never shortens it); after expiry it starts from today. Reactivates
    a suspended tenant."""
    hospital = get_object_or_404(Hospital, pk=pk)
    if request.method != 'POST':
        return redirect('saas:hospital_detail', pk=pk)
    try:
        months = int(request.POST.get('months') or 1)
    except (TypeError, ValueError):
        months = 1
    months = max(1, min(months, 60))
    today = timezone.localdate()
    base = hospital.expiry_date if hospital.expiry_date > today else today
    new_expiry = _add_months(base, months)

    from decimal import Decimal, InvalidOperation
    raw_amount = request.POST.get('amount')
    try:
        amount = Decimal(str(raw_amount)) if raw_amount not in (None, '') else None
    except InvalidOperation:
        amount = None
    if amount is None:
        amount = (hospital.monthly_price or Decimal('0')) * months
    note = (request.POST.get('note') or '').strip() or \
        f"Renewal — {months} month(s) → {new_expiry:%d %b %Y}"

    payment = HospitalPayment.objects.create(
        hospital=hospital, amount=amount, payment_date=today, note=note)
    hospital.expiry_date = new_expiry
    hospital.is_active = True                    # renewing lifts a suspension
    hospital.save(update_fields=['expiry_date', 'is_active'])
    messages.success(
        request, f"{hospital.name} renewed to {new_expiry:%d %b %Y}. Payment recorded.")
    return redirect('saas:payment_invoice', pk=payment.pk)


@superuser_required
def payment_invoice(request, pk):
    """Printable subscription invoice / receipt for one HospitalPayment."""
    payment = get_object_or_404(HospitalPayment.objects.select_related('hospital'), pk=pk)
    return render(request, 'saas/payment_invoice.html', {'payment': payment})


def _load_license_private_key():
    """The private signing key for desktop/LAN licences. Read from a JSON file next
    to the licensing tools, or (on the hosted host, where the repo file is not
    present) the env var DESKTOP_LICENSE_PRIVATE_KEY. Returns None if neither is set
    — the page then explains how to install it rather than crashing."""
    import json
    from pathlib import Path
    from django.conf import settings as dj_settings

    env = os.getenv("DESKTOP_LICENSE_PRIVATE_KEY")
    if env:
        try:
            return json.loads(env)
        except Exception:
            return None
    for p in (Path(dj_settings.BASE_DIR) / "licensing" / "private_key.json",
              Path(dj_settings.DATA_DIR) / "private_key.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


@superuser_required
def desktop_license(request):
    """Generate an offline desktop/LAN licence key from the hosted owner portal —
    the same key `licensing/sign_license.py` prints, but from the web so the owner
    can issue one per install without a shell. Signing needs the PRIVATE key, which
    lives on this host only (env or file, never in the repo)."""
    from user_mgmt.licensing import make_token

    priv = _load_license_private_key()
    token = clinic = None
    months = 1
    if request.method == 'POST':
        if priv is None:
            messages.error(request, "No signing key installed on this server. Set "
                           "DESKTOP_LICENSE_PRIVATE_KEY, or upload private_key.json.")
        else:
            try:
                months = max(1, min(int(request.POST.get('months') or 1), 60))
            except (TypeError, ValueError):
                months = 1
            extra = {}
            # Pick a hospital from the dropdown -> its name + slug bind the key to it;
            # otherwise fall back to a typed name.
            hospital_id = request.POST.get('hospital')
            if hospital_id:
                h = Hospital.objects.filter(pk=hospital_id).first()
                if h:
                    clinic = h.name
                    extra['slug'] = h.slug
            if not clinic:
                clinic = (request.POST.get('clinic') or '').strip() or 'Clinic'
            machine = (request.POST.get('machine') or '').strip()
            if machine:
                extra['machine'] = machine
            today = timezone.localdate()
            token = make_token(clinic, _add_months(today, months), today, priv,
                               extra=extra or None)

    return render(request, 'saas/desktop_license.html', {
        'have_key': priv is not None, 'token': token,
        'clinic': clinic, 'months': months,
        'hospitals': Hospital.objects.all().order_by('name'),
    })


@superuser_required
def install_signing_key(request):
    """Install the licence signing key straight from the portal — no cPanel needed.

    The superuser uploads (or pastes) the `private_key.json` that this project's
    `licensing/keygen.py` produced; it is verified to match THIS build's public key
    (so the licences it signs will actually activate), then written to
    ``DATA_DIR/private_key.json`` where `_load_license_private_key` finds it. Stored
    outside any web-served path, and never in the repo."""
    import json
    from datetime import date
    from pathlib import Path
    from django.conf import settings as dj_settings
    from user_mgmt.licensing import make_token, read_token

    if request.method == 'POST':
        f = request.FILES.get('keyfile')
        pasted = (request.POST.get('keytext') or '').strip()
        try:
            if f:
                data = json.loads(f.read().decode('utf-8'))
            elif pasted:
                data = json.loads(pasted)
            else:
                raise ValueError("choose the private_key.json file, or paste its contents")
            if not all(k in data for k in ('n', 'e', 'd')):
                raise ValueError("this is not a private key (it needs n, e and d)")
            # Prove it is the private half of THIS build's public key: sign a probe
            # and verify it with the embedded PUBLIC_KEY. A mismatched key would sign
            # licences the app then rejects.
            probe = make_token('probe', date.today(), date.today(),
                               {'n': int(data['n']), 'e': int(data['e']), 'd': int(data['d'])})
            if read_token(probe) is None:
                raise ValueError("this key does not match this build — licences it signs "
                                 "would not activate. Upload the private_key.json made by "
                                 "this project's keygen.")
            dest = Path(dj_settings.DATA_DIR) / 'private_key.json'
            dest.write_text(json.dumps({'n': int(data['n']), 'e': int(data['e']),
                                        'd': int(data['d'])}), encoding='utf-8')
            messages.success(request, "Signing key installed. You can generate licences now.")
            return redirect('saas:desktop_license')
        except Exception as exc:
            messages.error(request, f"Could not install that key: {exc}")

    return render(request, 'saas/install_signing_key.html',
                  {'have_key': _load_license_private_key() is not None})


MAX_BACKUP_BYTES = 200 * 1024 * 1024      # 200 MB — a clinic SQLite + media, zipped
# Keep only the newest snapshot per install: the client uploads a full copy each time
# and only when the data has changed, so one file per clinic is all the host needs —
# older ones are dropped so the host disk does not grow with every launch.
KEEP_BACKUPS_PER_INSTALL = 1


@csrf_exempt
def backup_upload(request):
    """Receive a full-data backup zip from an offline desktop/LAN install.

    Called by `desktop.launcher`, not a browser — so it is CSRF-exempt and
    session-less. Authentication is the install's **signed licence key**: only a
    token signed by the owner's private key verifies, which proves it is a real
    licensed install (expiry is *not* required — we want a lapsed clinic's data
    too). The clinic name in the token labels the backup. Kept to the last few per
    install so the host disk stays bounded. This stores the file as-is; it is never
    merged into the hosted database (that would be live sync, a separate thing)."""
    from django.http import JsonResponse
    from user_mgmt.licensing import read_token

    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    data = read_token(request.POST.get('token') or '')
    if data is None:
        return JsonResponse({'error': 'invalid or missing licence'}, status=403)
    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'error': 'no file'}, status=400)
    if upload.size > MAX_BACKUP_BYTES:
        return JsonResponse({'error': 'file too large'}, status=413)

    install = (data.get('clinic') or 'Unknown').strip()[:255]
    backup = DesktopBackup.objects.create(
        install_name=install, file=upload, size_bytes=upload.size)

    # Rotate: keep only the most recent few for this install, deleting the file too.
    old = DesktopBackup.objects.filter(install_name=install) \
        .order_by('-uploaded_at')[KEEP_BACKUPS_PER_INSTALL:]
    for b in list(old):
        b.file.delete(save=False)
        b.delete()
    return JsonResponse({'ok': True, 'id': backup.pk})


@superuser_required
def backup_list(request):
    """SaaS owner view of every desktop/LAN install's uploaded backups, grouped by
    install, newest first — the copies to hand back if a clinic loses its computer."""
    backups = list(DesktopBackup.objects.all())
    groups = {}
    for b in backups:
        groups.setdefault(b.install_name, []).append(b)
    installs = [{'name': name, 'backups': items,
                 'latest': items[0].uploaded_at if items else None}
                for name, items in sorted(groups.items())]
    return render(request, 'saas/backup_list.html',
                  {'installs': installs, 'total': len(backups)})


@superuser_required
def backup_download_file(request, pk):
    """Download one stored backup zip to hand back to a clinic for restore."""
    from django.http import FileResponse, Http404
    backup = get_object_or_404(DesktopBackup, pk=pk)
    try:
        return FileResponse(backup.file.open('rb'), as_attachment=True,
                            filename=backup.file.name.split('/')[-1])
    except FileNotFoundError:
        raise Http404("Backup file is missing on the server.")


def render_hospital_login(request, hospital):
    """The tenant login page + POST handling for one hospital.

    Shared by the path route (`/<slug>/login/`) and the subdomain route
    (`<slug>.<BASE_DOMAIN>/login/`), so both behave identically: branded with the
    hospital's own name/logo/colour, and **isolated** — an account belonging to
    another hospital is rejected here even with a correct password (only that
    hospital's staff, or a superuser, may sign in on its portal).
    """
    from accounts.lockout import guard
    from user_mgmt.models import SiteSettings

    # Same brute-force limit as the platform door — a tenant portal is just as
    # exposed, and this is the address a hospital's staff actually use.
    locked = guard(request)
    if locked is not None:
        return locked

    # Check if subscription is active
    if not hospital.is_active or hospital.expiry_date < timezone.now().date():
        return render(request, 'saas/suspended.html', {'hospital': hospital})

    from saas.utils import set_current_hospital, clear_current_hospital
    set_current_hospital(hospital)
    try:
        branding = SiteSettings.load()
    finally:
        clear_current_hospital()

    error_message = None
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, email=email, password=password)
        if user is not None:
            # Verify the user belongs to this hospital (or is a superuser)
            if user.is_superuser or user.hospital == hospital:
                auth_login(request, user)
                return redirect('user_mgmt:post_login_redirect')
            else:
                error_message = f"This account does not belong to {hospital.name}."
        else:
            error_message = "Invalid email or password."

    return render(request, 'saas/login.html', {
        'hospital': hospital,
        'branding': branding,
        'error_message': error_message
    })


def hospital_login(request, hospital_slug):
    hospital = get_object_or_404(Hospital, slug=hospital_slug)
    return render_hospital_login(request, hospital)


def tenant_login_url(request, hospital):
    """Absolute login URL for a hospital — its subdomain when a real base domain
    is configured (`<slug>.<BASE_DOMAIN>/login/`), else the path form
    (`/<slug>/login/`) for dev / LAN / hosts without wildcard DNS."""
    from django.conf import settings
    base = (getattr(settings, 'BASE_DOMAIN', '') or '').lower()
    if base and '.' in base and base != 'localhost':
        scheme = 'https' if request.is_secure() else 'http'
        return f"{scheme}://{hospital.slug}.{base}/login/"
    return request.build_absolute_uri(f"/{hospital.slug}/login/")
