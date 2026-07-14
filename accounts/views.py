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
