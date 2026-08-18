"""What the system tried to send, and what happened to it."""
from django.shortcuts import render

from accounts.decorators import feature_required, role_required
from pharma_mgmt.pagination import paginate

from .models import MessageLog
from .services import email_configured, sms_configured


@feature_required('settings')
@role_required(['ADMIN'])
def message_log(request):
    """Settings → Messages.

    Sends happen inline (there is no job queue on this host), so a failure has
    nowhere to retry from and nobody watching. This screen is the only place a
    failed reminder becomes visible — without it, "the patient never got the
    message" cannot be answered.

    It also states plainly whether each channel is configured at all, because
    the commonest cause of "nothing is being sent" is that nothing was ever set
    up, and a page full of SKIPPED rows does not say that on its own.
    """
    qs = MessageLog.objects.all()
    status = request.GET.get('status', '')
    kind = request.GET.get('kind', '')
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    return render(request, 'messaging/message_log.html', {
        'page': paginate(request, qs),
        'status': status,
        'kind': kind,
        # `.order_by()` before `.distinct()` is load-bearing: Meta.ordering is
        # ('-created_at',), and Django adds an ORDER BY column to the SELECT, so
        # `values_list('kind').distinct()` was really DISTINCT (kind, created_at)
        # — one row per message, and the dropdown listed 'lab_ready' twice.
        'kinds': [(k, MessageLog.KIND_LABELS.get(
                      k, k.replace('_', ' ').capitalize()))
                  for k in sorted(set(
                      MessageLog.objects.order_by()
                      .values_list('kind', flat=True).distinct())) if k],
        'email_ready': email_configured(),
        'sms_ready': sms_configured(),
        'failed_count': MessageLog.objects.filter(status=MessageLog.FAILED).count(),
    })
