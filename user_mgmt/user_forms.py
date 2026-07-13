from django import forms

from accounts.models import User


class UserForm(forms.ModelForm):
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Required for a new user. On edit, leave blank to keep the current password.")

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role', 'is_active']
