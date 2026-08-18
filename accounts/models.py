from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UserManager


# An employee who does not sign in. See ROLE_CHOICES below.
NO_LOGIN_ROLE = 'NOLOGIN'
# The domain their placeholder address is minted in. Reserved by RFC 2606, so it
# can never resolve and can never be emailed by accident.
NO_LOGIN_EMAIL_DOMAIN = '@no-login.invalid'


class User(AbstractUser):
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    ROLE_CHOICES = (
        ('ADMIN', 'Admin'),
        ('RECEPTIONIST', 'Receptionist'),
        ('DOCTOR', 'Doctor'),
        ('NURSE', 'Ward Staff / Nurse'),
        ('PHARMACIST', 'Pharmacist'),
        ('WHOLESALE', 'Wholesale Operator'),
        ('LABTECH', 'Lab Technician'),
        ('SONOGRAPHER', 'Sonographer'),
        ('ACCOUNTANT', 'Accountant'),
        # Most of a clinic's payroll does not sign in — a guard, a cleaner, a
        # ward boy, a driver. They still need a User row, because Attendance,
        # LeaveRequest and SalaryPayment all point at one, but they are not a
        # pharmacist and must not be shown as one. No entry in
        # `permissions.FEATURES` names this role, so it grants nothing on its
        # own; `hr.forms.EmployeeForm` additionally sets `custom_features = []`
        # and an unusable password, so three independent things would each have
        # to be wrong before such an account could get in.
        (NO_LOGIN_ROLE, 'No system access'),
    )

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='PHARMACIST')
    # per-user access override: None = use the role's default features;
    # a list = the exact set of features this user may access.
    custom_features = models.JSONField(null=True, blank=True, default=None)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    objects = UserManager()

    @property
    def signs_in(self):
        """False for a payroll-only employee (see NO_LOGIN_ROLE)."""
        return not (self.email or '').endswith(NO_LOGIN_EMAIL_DOMAIN)

    @property
    def display_email(self):
        """The address to SHOW. Blank for a payroll-only employee, whose address
        is a generated placeholder — it was rendered verbatim in the staff list,
        the users list and the Pay Salary dropdown, where a ward boy appeared as
        `staff-d448b32bae@no-login.invalid`."""
        return self.email if self.signs_in else ''

    @property
    def display_name(self):
        """Name first, address only as a fallback — and never the placeholder.

        `get_full_name()` is blank for a user with no name set, which is why so
        many screens fell back to the email and ended up printing the
        placeholder. This never does."""
        name = (self.get_full_name() or '').strip()
        if name:
            return name
        return self.email if self.signs_in else 'Unnamed employee'

    @property
    def initials(self):
        """For the avatar circle. Taken from the NAME, not the address — every
        demo user showed a "D" because their addresses all began `demo.`, and
        payroll-only staff showed "S" for `staff-`."""
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return ''.join(p[0] for p in parts[:2]).upper()
        name = (self.get_full_name() or '').strip()
        if name:
            return ''.join(w[0] for w in name.split()[:2]).upper()
        return (self.email[:1] or '?').upper() if self.signs_in else '—'

    def __str__(self):
        if not self.signs_in:
            return f"{self.display_name} (no system access)"
        return f"{self.email} ({self.get_role_display()})"

    def effective_features(self):
        from .permissions import effective_features
        return effective_features(self)

    def has_feature(self, key):
        from .permissions import user_has_feature
        return user_has_feature(self, key)

    @property
    def is_customized(self):
        return self.custom_features is not None

    @property
    def is_admin(self):
        return self.role == 'ADMIN' or self.is_superuser

    @property
    def is_pharmacist(self):
        return self.role == 'PHARMACIST'

    @property
    def is_receptionist(self):
        return self.role == 'RECEPTIONIST'

    @property
    def is_doctor(self):
        return self.role == 'DOCTOR'

    @property
    def is_accountant(self):
        return self.role == 'ACCOUNTANT'


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    link = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Notification for {self.user.email}: {self.message[:30]}"

    def save(self, *args, **kwargs):
        """One chokepoint for every creation path, so a syncing offline queue
        cannot ring the bell once per queued entry (see `accounts/replay.py`)."""
        if self._state.adding:
            from accounts.replay import stamp
            stamp(self)
        return super().save(*args, **kwargs)

    @classmethod
    def send_to_role(cls, hospital, role, message, link='', force=False):
        """Notify everyone in a role.

        `force=True` means "this is still outstanding whenever it was raised" — a
        short-stock sale the pharmacist must still go and count. Those stay unread
        through an offline replay; everything else is filed as history.
        """
        if not hospital:
            return
        users = User.objects.filter(hospital=hospital, role=role, is_active=True)
        for u in users:
            note = cls(user=u, message=message, link=link)
            if force:
                note.needs_action = True
            note.save()

    @classmethod
    def notify_admins(cls, hospital, message, link=''):
        """Tell the owner about something they cannot find out any other way.

        Kept for the *exceptional* — stock written off, a bill voided, someone
        guessing at a password. Routine traffic (every sale, every appointment)
        belongs on the admin overview instead: an inbox that fills up with normal
        activity is an inbox nobody reads, which costs more than it gives.
        """
        cls.send_to_role(hospital, 'ADMIN', message, link)