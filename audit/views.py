from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.shortcuts import render

from accounts.decorators import feature_required
from .models import AuditLog


@feature_required('audit')
def audit_log(request):
    # `AuditLog.objects` is a TenantManager, so this is already this hospital's
    # trail only — and fail-closed for a hospital-less non-superuser.
    logs = AuditLog.objects.select_related('user')

    action = request.GET.get('action', '').strip()
    model = request.GET.get('model', '').strip()
    user_id = request.GET.get('user', '').strip()
    q = request.GET.get('q', '').strip()

    if action:
        logs = logs.filter(action=action)
    if model:
        logs = logs.filter(model_name=model)
    if user_id and user_id.isdigit():
        logs = logs.filter(user_id=user_id)
    if q:
        logs = logs.filter(object_repr__icontains=q)

    paginator = Paginator(logs, 40)
    page = paginator.get_page(request.GET.get('page'))

    # Both dropdowns are built from the SCOPED queryset — a filter list naming
    # another tenant's staff leaks just as surely as the rows would.
    models = (AuditLog.objects.values_list('model_name', flat=True)
              .distinct().order_by('model_name'))
    User = get_user_model()
    users = (User.objects
             .filter(pk__in=AuditLog.objects.exclude(user__isnull=True)
                     .values_list('user_id', flat=True))
             .distinct().order_by('email'))

    return render(request, 'audit/audit_log.html', {
        'page': page,
        'actions': AuditLog.ACTIONS,
        'models': models,
        'users': users,
        'f': {'action': action, 'model': model, 'user': user_id, 'q': q},
    })
