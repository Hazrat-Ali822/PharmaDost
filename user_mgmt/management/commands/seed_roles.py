from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.apps import apps


ROLE_PERMS = {
'Admin': {
'lab': ['add', 'change', 'delete', 'view'],
'imaging': ['add', 'change', 'delete', 'view'],
},
'Manager': {
# Patients + create orders/referrals, but not edit results
'lab': ['add', 'change', 'view'], # can create orders (TestOrder)
'imaging': ['add', 'change', 'view'], # can create exams (draft)
},
'Pharmacist': {
# Pharmacy app (when you add it) – placeholder; nothing in lab/imaging
},
'LabTech': {
'lab': ['add', 'change', 'view'], # full lab workflow
},
'Sonographer': {
'imaging': ['add', 'change', 'view'], # full US workflow
},
}


class Command(BaseCommand):
	help = 'Create role groups and assign model permissions to them.'

	def handle(self, *args, **kwargs):
		created = 0
		for role, apps_perms in ROLE_PERMS.items():
			group, g_created = Group.objects.get_or_create(name=role)
			if g_created:
				created += 1
			# clear then set
			group.permissions.clear()
			for app_label, actions in apps_perms.items():
				for model in apps.get_app_config(app_label).get_models():
					opts = model._meta
					for action in actions:
						codename = f'{action}_{opts.model_name}'
						try:
							perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
							group.permissions.add(perm)
						except Permission.DoesNotExist:
							continue
			group.save()
		self.stdout.write(self.style.SUCCESS(f'Roles seeded. New groups: +{created}'))