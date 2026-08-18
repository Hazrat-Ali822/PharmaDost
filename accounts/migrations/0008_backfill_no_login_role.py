"""Correct the staff already created as "Pharmacist" because they have no login.

`hr.forms.EmployeeForm` mints a `User` for a payroll-only employee — a guard, a
cleaner, a ward boy — because Attendance, LeaveRequest and SalaryPayment all
point at one. It set `role='PHARMACIST'`, which was never a decision: it is
simply the model default, copied. The consequence was visible on four screens —
the staff list, the users list, the attendance sheet and the Pay Salary picker
all showed a ward boy as a Pharmacist.

Identified by the address, not by the role: `@no-login.invalid` is minted by
that one code path and is reserved by RFC 2606, so it cannot collide with a real
account. Anyone who really is a pharmacist has a real address and is untouched.
"""
from django.db import migrations

NO_LOGIN_ROLE = 'NOLOGIN'
DOMAIN = '@no-login.invalid'


def forwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(email__endswith=DOMAIN).exclude(
        role=NO_LOGIN_ROLE).update(role=NO_LOGIN_ROLE)


def backwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(email__endswith=DOMAIN, role=NO_LOGIN_ROLE).update(
        role='PHARMACIST')


class Migration(migrations.Migration):

    dependencies = [('accounts', '0007_user_no_login_role')]

    operations = [migrations.RunPython(forwards, backwards)]
