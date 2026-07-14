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
    """AJAX endpoint to retrieve latest unread notifications."""
    from django.template.loader import render_to_string
    unread = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:5]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    
    html = render_to_string('partials/notifications_list.html', {
        'unread_notifications': unread,
        'unread_notifications_count': unread_count
    }, request=request)
    
    return JsonResponse({
        'count': unread_count,
        'html': html
    })
