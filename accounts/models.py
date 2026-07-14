from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UserManager


class User(AbstractUser):
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    ROLE_CHOICES = (
        ('ADMIN', 'Admin'),
        ('RECEPTIONIST', 'Receptionist'),
        ('DOCTOR', 'Doctor'),
        ('PHARMACIST', 'Pharmacist'),
        ('WHOLESALE', 'Wholesale Operator'),
        ('LABTECH', 'Lab Technician'),
        ('SONOGRAPHER', 'Sonographer'),
        ('ACCOUNTANT', 'Accountant'),
    )

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='PHARMACIST')
    # per-user access override: None = use the role's default features;
    # a list = the exact set of features this user may access.
    custom_features = models.JSONField(null=True, blank=True, default=None)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    objects = UserManager()

    def __str__(self):
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

    @classmethod
    def send_to_role(cls, hospital, role, message, link=''):
        if not hospital:
            return
        users = User.objects.filter(hospital=hospital, role=role, is_active=True)
        for u in users:
            cls.objects.create(
                user=u,
                message=message,
                link=link
            )