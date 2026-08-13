from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.shortcuts import redirect
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
