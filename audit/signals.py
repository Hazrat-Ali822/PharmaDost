"""Automatic audit logging via signals.

Only changes made by an authenticated user through a web request are logged
(system/seed/shell actions have no request user and are skipped), keeping the
log to genuine accountable user activity.
"""
from django.contrib.auth.signals import (user_logged_in, user_logged_out,
                                          user_login_failed)
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .middleware import get_current_user
from .models import AuditLog

# app_label.ModelName of the business objects we track (line-items excluded to
# avoid noise; the parent create/update covers them)
TRACKED = {
    'sales.Sale',
    'inventory.Medicine', 'inventory.PurchaseOrder', 'inventory.StockAdjustment',
    'inventory.PurchaseReturn',
    'billing.Invoice', 'billing.Expense', 'billing.CashClosing',
    'opd.Doctor', 'opd.Appointment', 'opd.DoctorPayout', 'opd.ClinicalRecord',
    'patients.Patient',
    'customers.Customer', 'customers.CustomerPayment',
    'suppliers.Supplier', 'suppliers.SupplierPayment',
    'lab.TestOrder', 'imaging.ImagingStudy',
    'accounts.User',
}


def _label(sender):
    return f'{sender._meta.app_label}.{sender.__name__}'


def _actor():
    user = get_current_user()
    if user and getattr(user, 'is_authenticated', False):
        return user
    return None


@receiver(post_save)
def _log_save(sender, instance, created, **kwargs):
    if _label(sender) not in TRACKED:
        return
    user = _actor()
    if user is None:
        return
    AuditLog.objects.create(
        user=user,
        action='CREATE' if created else 'UPDATE',
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
    )


@receiver(post_delete)
def _log_delete(sender, instance, **kwargs):
    if _label(sender) not in TRACKED:
        return
    user = _actor()
    if user is None:
        return
    AuditLog.objects.create(
        user=user,
        action='DELETE',
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
    )


@receiver(user_logged_in)
def _log_login(sender, request, user, **kwargs):
    AuditLog.objects.create(user=user, action='LOGIN', model_name='User',
                            object_id=str(user.pk), object_repr=str(user))


@receiver(user_logged_out)
def _log_logout(sender, request, user, **kwargs):
    if user is None:
        return
    AuditLog.objects.create(user=user, action='LOGOUT', model_name='User',
                            object_id=str(user.pk), object_repr=str(user))


@receiver(user_login_failed)
def _log_login_failed(sender, credentials, **kwargs):
    who = credentials.get('username') or credentials.get('email') or '—'
    AuditLog.objects.create(user=None, action='LOGIN_FAILED', model_name='User',
                            object_repr=str(who)[:200], description='Failed login attempt')
