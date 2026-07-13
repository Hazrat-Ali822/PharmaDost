from django.core.management.base import BaseCommand
from user_mgmt.models import Organization

class Command(BaseCommand):
    help = "Create a default Organization if none exists."

    def handle(self, *args, **options):
        org, created = Organization.objects.get_or_create(name="Default Clinic")
        if created:
            self.stdout.write(self.style.SUCCESS(f"Organization created: {org.name}"))
        else:
            self.stdout.write(self.style.WARNING(f"Organization already exists: {org.name}"))
