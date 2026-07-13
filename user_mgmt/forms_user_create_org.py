from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.text import capfirst
from .models import Organization


ROLE_ADMIN='Admin'; ROLE_MANAGER='Manager'; ROLE_PHARMACIST='Pharmacist'; ROLE_LABTECH='LabTech'; ROLE_SONOGRAPHER='Sonographer'
User = get_user_model()


def model_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


class CreateUserForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, required=True)
    role = forms.ChoiceField(choices=[
    (ROLE_MANAGER, 'Manager'),
    (ROLE_PHARMACIST, 'Pharmacist'),
    (ROLE_LABTECH, 'Lab Technician'),
    (ROLE_SONOGRAPHER, 'Sonographer'),
    (ROLE_ADMIN, 'Admin'),
])
organization = forms.ModelChoiceField(queryset=Organization.objects.all(), required=False)


def __init__(self, *args, **kwargs):
    self.request = kwargs.pop('request', None)
    super().__init__(*args, **kwargs)
    login_field_name = User.USERNAME_FIELD
    self.fields[login_field_name] = forms.CharField(label=capfirst(login_field_name.replace('_',' ')), required=True)
    if login_field_name != 'email' and model_has_field(User, 'email'):
        self.fields['email'] = forms.EmailField(required=False)
# Non‑superusers cannot choose org or create Admins
    if not (self.request and self.request.user.is_superuser):
        self.fields['role'].choices = [c for c in self.fields['role'].choices if c[0] != 'Admin']
        self.fields['organization'].widget = forms.HiddenInput()
    if hasattr(self.request.user, 'profile') and self.request.user.profile.organization:
        self.initial['organization'] = self.request.user.profile.organization


def save(self):
    cd = self.cleaned_data
    login_field_name = User.USERNAME_FIELD
    create_kwargs = {login_field_name: cd[login_field_name], 'password': cd['password']}
    if 'email' in cd and cd['email']:
        create_kwargs['email'] = cd['email']
        user = User._default_manager.create_user(**create_kwargs)
# org assignment
    org = cd.get('organization')
    if not org and hasattr(self.request.user, 'profile'):
        org = getattr(self.request.user.profile, 'organization', None)
    if org and hasattr(user, 'profile'):
        return user