from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login
from django.contrib import messages
from django.shortcuts import redirect
from .models import Notification

DEMO_EMAIL = 'demo@sehatyar.online'


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
