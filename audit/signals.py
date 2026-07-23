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


def _hospital_for(instance, user):
    """Which tenant this entry belongs to.

    Prefer the affected object's own hospital over the actor's: when a superuser
    edits a tenant's record, the trail belongs to that tenant — filing it under
    the actor (who has no hospital) would hide it from the admin it concerns.
    Falls back to walking one relation, since several models carry their tenancy
    through `patient` rather than a column of their own.
    """
    hospital_id = getattr(instance, 'hospital_id', None)
    if hospital_id:
        return hospital_id
    for attr in ('patient', 'admission', 'appointment'):
        related = getattr(instance, attr, None)
        if related is not None and getattr(related, 'hospital_id', None):
            return related.hospital_id
    return getattr(user, 'hospital_id', None)


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
        hospital_id=_hospital_for(instance, user),
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
        hospital_id=_hospital_for(instance, user),
    )


@receiver(user_logged_in)
def _log_login(sender, request, user, **kwargs):
    AuditLog.objects.create(user=user, action='LOGIN', model_name='User',
                            object_id=str(user.pk), object_repr=str(user),
                            hospital_id=getattr(user, 'hospital_id', None))


@receiver(user_logged_out)
def _log_logout(sender, request, user, **kwargs):
    if user is None:
        return
    AuditLog.objects.create(user=user, action='LOGOUT', model_name='User',
                            object_id=str(user.pk), object_repr=str(user),
                            hospital_id=getattr(user, 'hospital_id', None))


@receiver(user_login_failed)
def _log_login_failed(sender, credentials, **kwargs):
    """Nobody is logged in here, so the tenant has to come from the address that
    was tried. Without it the row files under no hospital and the admin whose
    staff account is being guessed at never sees the attempts."""
    from accounts.models import User

    who = credentials.get('username') or credentials.get('email') or '—'
    hospital_id = (User.objects.filter(email__iexact=str(who))
                   .values_list('hospital_id', flat=True).first())
    AuditLog.objects.create(user=None, action='LOGIN_FAILED', model_name='User',
                            object_repr=str(who)[:200], description='Failed login attempt',
                            hospital_id=hospital_id)

    if hospital_id:
        _warn_on_repeated_failures(who, hospital_id)


# A single fat-fingered password is noise; a run of them against one account is
# somebody trying to get in, and only the owner can act on that.
FAILED_LOGIN_BURST = 3
FAILED_LOGIN_WINDOW_MINUTES = 15


def _warn_on_repeated_failures(who, hospital_id):
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import Notification
    from saas.models import Hospital

    since = timezone.now() - timedelta(minutes=FAILED_LOGIN_WINDOW_MINUTES)
    recent = AuditLog.all_objects.filter(
        action='LOGIN_FAILED', hospital_id=hospital_id,
        object_repr=str(who)[:200], timestamp__gte=since).count()
    # Fires once, on the attempt that crosses the line — not on every one after.
    if recent != FAILED_LOGIN_BURST:
        return
    hospital = Hospital.objects.filter(pk=hospital_id).first()
    Notification.notify_admins(
        hospital=hospital,
        message=(f"🔒 {recent} failed sign-in attempts for {who} in the last "
                 f"{FAILED_LOGIN_WINDOW_MINUTES} minutes."),
        link='/audit/?action=LOGIN_FAILED')
