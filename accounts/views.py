from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import Notification

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
