# user_mgmt/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.text import capfirst

from .roles import (
    ROLE_ADMIN, ROLE_MANAGER, ROLE_PHARMACIST, ROLE_LABTECH, ROLE_SONOGRAPHER
)

User = get_user_model()

def model_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False

class CreateUserForm(forms.Form):
    """
    Works with any custom USER model.
    Dynamically adds the correct login field (User.USERNAME_FIELD),
    plus 'email' if it exists and is different.
    """
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    role = forms.ChoiceField(choices=[
        (ROLE_MANAGER, 'Manager'),
        (ROLE_PHARMACIST, 'Pharmacist'),
        (ROLE_LABTECH, 'Lab Technician'),
        (ROLE_SONOGRAPHER, 'Sonographer'),
        (ROLE_ADMIN, 'Admin'),  # hidden for non-superusers in __init__
    ])

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        # Dynamically add the login/identifier field
        login_field_name = User.USERNAME_FIELD  # e.g., "email" in many custom models
        login_label = capfirst(login_field_name.replace("_", " "))
        self.fields[login_field_name] = forms.CharField(
            label=login_label,
            required=True,
        )

        # Add email if present on model and not already the login field
        if login_field_name != "email" and model_has_field(User, "email"):
            self.fields["email"] = forms.EmailField(required=False)

        # Hide Admin role unless current user is superuser
        if not (self.request and self.request.user and self.request.user.is_superuser):
            self.fields["role"].choices = [
                c for c in self.fields["role"].choices if c[0] != ROLE_ADMIN
            ]

    def save(self):
        cd = self.cleaned_data
        login_field_name = User.USERNAME_FIELD
        login_value = cd[login_field_name]
        email_value = cd.get("email")

        # Build kwargs for create_user based on your model fields
        create_kwargs = {login_field_name: login_value, "password": cd["password"]}
        if email_value is not None:
            create_kwargs["email"] = email_value

        # Create user via the model's manager (works for custom models)
        user = User._default_manager.create_user(**create_kwargs)

        # is_staff policy: Admin & Manager can access Django admin; others cannot
        role = cd["role"]
        if role in (ROLE_ADMIN, ROLE_MANAGER):
            user.is_staff = True
            user.save(update_fields=["is_staff"])

        # Add to the selected group
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)

        return user
