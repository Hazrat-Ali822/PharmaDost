from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.safestring import mark_safe
from .models import Notification

DEMO_EMAIL = 'demo@sehatyar.online'


class RootLoginView(LoginView):
    """Sign-in on the bare platform domain (e.g. sehatyar.online).

    Only the platform owner (a superuser) may sign in here. A hospital's own
    staff are turned away with a link to their hospital portal — every tenant
    signs in from its own address (`<slug>.<BASE_DOMAIN>`), so the front door of
    the platform belongs to the owner alone. The public demo button below the
    form is unaffected (it goes through `demo_login`, not this view).
    """
    template_name = 'registration/login.html'

    def form_valid(self, form):
        user = form.get_user()
        if not user.is_superuser:
            # Correct credentials, but this is not the owner: do NOT log in here.
            from saas.views import tenant_login_url
            if getattr(user, 'hospital_id', None):
                link = tenant_login_url(self.request, user.hospital)
                form.add_error(None, mark_safe(
                    'Please sign in from your hospital portal: '
                    f'<a href="{link}">{link}</a>'))
            else:
                form.add_error(None, 'This account cannot sign in here — '
                                     'please contact your administrator.')
            return self.form_invalid(form)
        return super().form_valid(form)


def smart_login(request, *args, **kwargs):
    """The `/login/` route, dispatched by host.

    A hospital subdomain (`<slug>.<BASE_DOMAIN>`) renders that hospital's
    branded, isolated login; the bare platform domain renders the owner sign-in.
    The path form `/<slug>/login/` still works too (a fallback for hosts without
    wildcard DNS yet), via `saas.views.hospital_login`.
    """
    from django.conf import settings

    from .lockout import guard
    # Guessing a password had no consequence at all before this: the audit log
    # noticed a burst and told the admin, but nothing refused the next attempt.
    locked = guard(request)
    if locked is not None:
        return locked

    # Already signed in? Send them where they work. Every branch below renders a
    # sign-in form, which to somebody who is signed in reads as "you have been
    # logged out" — and on a phone, where /login/ is one mis-tap from the app
    # icon, that is a genuinely alarming thing to land on.
    if request.user.is_authenticated:
        return redirect('user_mgmt:post_login_redirect')
    # Desktop / LAN build: one clinic, no SaaS owner. Staff reach it at localhost
    # or the LAN IP, and neither resolves a tenant by host — so the owner-only
    # RootLoginView below would lock out EVERY non-superuser (nurse, receptionist,
    # doctor) the clinic creates, leaving only the first-run superuser able to
    # sign in on the wifi. There is no platform front door to protect here, so use
    # the plain login that admits any active user.
    if getattr(settings, 'DESKTOP_BUILD', False):
        return LoginView.as_view(template_name='registration/login.html')(
            request, *args, **kwargs)
    from saas.utils import hospital_from_host
    hospital = hospital_from_host(request.get_host())
    if hospital is not None:
        from saas.views import render_hospital_login
        return render_hospital_login(request, hospital)
    return RootLoginView.as_view()(request, *args, **kwargs)


def demo_login(request):
    """One click, no password — sign the visitor in as the demo account so anyone
    can try the whole system. The demo user is a normal (non-superuser) admin
    scoped to its own Demo Hospital, so playing in it never touches real tenants.
    Seed the data with:  python manage.py seed_public_demo
    """
    user = Notification._meta.apps.get_model('accounts', 'User').objects.filter(
        email=DEMO_EMAIL).first()
    if not user:
        messages.error(request,
                       'The demo is being set up — please try again shortly.')
        return redirect('login')
    # No password check here by design; name the backend since we skip authenticate().
    auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    messages.info(request,
                  'You are exploring the live demo. Feel free to click around — '
                  'this is sample data, separate from any real hospital.')
    return redirect('/')

def demo_login_as(request, role):
    """Sign in as one of the demo tenant's other roles — still no password.

    `/demo/` has always signed any visitor in as the demo **admin**, which is the
    most privileged account that tenant has, so admitting its receptionist,
    doctor or nurse the same way adds no exposure at all. It is the same isolated
    hospital and the same sample data.

    It exists because seeing the system as a doctor or a pharmacist is most of
    what the demo is *for* — the admin's view is the least representative of the
    eight — and because a password box is a wall for anyone driving the demo
    without a keyboard, a browser agent included.

    Strictly scoped: the user must belong to the demo hospital and must not be a
    superuser. That query cannot return an account from a real tenant, which is
    the property that matters rather than any check on the role name.
    """
    from django.contrib.auth import get_user_model
    from saas.utils import DEMO_SLUG

    user = (get_user_model().objects
            .filter(hospital__slug=DEMO_SLUG, role=(role or '').upper(),
                    is_superuser=False, is_active=True)
            .order_by('pk').first())
    if not user:
        messages.error(request, 'That demo role is not set up. '
                                'Run: python manage.py seed_public_demo')
        return redirect('demo_login')

    auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    messages.info(request, f'Now viewing the demo as {user.get_role_display()} '
                           f'({user.get_full_name() or user.email}).')
    return redirect('user_mgmt:post_login_redirect')


@login_required
@require_POST
def mark_notifications_read(request):
    notification_id = request.POST.get('id')
    if notification_id:
        Notification.objects.filter(user=request.user, pk=notification_id).update(is_read=True)
    else:
        Notification.objects.filter(user=request.user).update(is_read=True)
    return JsonResponse({'status': 'ok'})


@login_required
def get_notifications_latest(request):
    """AJAX endpoint to retrieve latest unread notifications.

    Every logged-in browser polls this on a timer, so it must stay cheap. It is
    rendered WITHOUT `request=request` on purpose: passing the request builds a
    RequestContext, which runs every context processor — including the sidebar
    badge counts — turning a 2-query endpoint into a 15-query one on every poll.
    `partials/notifications_list.html` needs nothing but `unread_notifications`.
    """
    from django.template.loader import render_to_string
    unread = list(
        Notification.objects.filter(user=request.user, is_read=False)
        .order_by('-created_at')[:5]
    )
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

    html = render_to_string('partials/notifications_list.html', {
        'unread_notifications': unread,
        'unread_notifications_count': unread_count,
    })

    return JsonResponse({
        'count': unread_count,
        'html': html,
    })


def _reset_page_tenant(request, back):
    """Which hospital's door is this reset page standing next to?

    The page is reached from a tenant login, so it should carry that hospital's
    name and colour rather than the platform's — otherwise a nurse who taps
    "Forgot password" lands somewhere that looks like a different product.

    Two routes to the answer, because there are two routes to the login itself:
    the subdomain (`shaheen.sehatyar.online`) is in the host, and the path form
    (`sehatyar.online/shaheen/login/`, still the fallback wherever wildcard DNS
    is not set up yet) is only visible in the `next` we were handed.
    """
    from saas.utils import hospital_from_host

    hospital = hospital_from_host(request.get_host())
    if hospital is not None:
        return hospital
    parts = [p for p in (back or '').split('/') if p]
    if len(parts) == 2 and parts[1] == 'login':
        from saas.models import Hospital
        return Hospital.objects.filter(slug=parts[0]).first()
    return None


def custom_password_reset_request(request):
    """Staff password reset request.
    Instead of sending an email, notifies the user's Hospital Admin
    so they can reset the password directly.
    """
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    # Where "Back to sign in" goes. Validated against this host before it is
    # followed — an unchecked `next` turns the page into an open redirect, which
    # is a phishing step: the user is on the real hospital domain, asks for a
    # reset, and is handed a copy of the login form. Same rule as
    # `patients.views._safe_next`.
    back = request.POST.get('next') or request.GET.get('next') or ''
    if back and not url_has_allowed_host_and_scheme(
            back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = ''
    hospital = _reset_page_tenant(request, back)
    if not back:
        back = reverse('login')

    branding = None
    if hospital is not None:
        from saas.utils import set_current_hospital, clear_current_hospital
        from user_mgmt.models import SiteSettings
        set_current_hospital(hospital)
        try:
            branding = SiteSettings.load()
        finally:
            clear_current_hospital()

    success_msg = None
    error_msg = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        if not email:
            error_msg = 'Please enter your registered email address.'
        else:
            from accounts.models import User, Notification
            user = User.objects.filter(email__iexact=email, is_active=True).first()
            if user and user.hospital:
                user_name = user.get_full_name() or user.email
                msg = f"Password reset requested by {user_name} ({user.get_role_display()})."
                Notification.notify_admins(
                    hospital=user.hospital,
                    message=msg,
                    link=f"/manage/users/{user.pk}/edit/"
                )
                success_msg = "A password reset request has been sent to your Hospital Administrator. Please contact your administrator to reset your password."
            elif user and user.is_superuser:
                success_msg = "Superuser password resets must be done via CLI (python manage.py changepassword)."
            else:
                success_msg = "If an active account exists for this email, your Hospital Administrator has been notified."

    ctx = {'success_msg': success_msg, 'error_msg': error_msg,
           'back_url': back, 'hospital': hospital}
    # Only override the context processor's `branding` when a tenant was
    # actually resolved — on the platform door it must stay the platform's.
    if branding is not None:
        ctx['branding'] = branding
    return render(request, 'registration/password_reset_request.html', ctx)

