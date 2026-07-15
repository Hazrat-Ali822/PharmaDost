from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


# Defaults used for the initial values AND the "Reset to default" action.
SITE_DEFAULTS = {
    "brand_name": "PharmaDost",
    "brand_tagline": "Hospital & Pharmacy",
    "logo_text": "P",
    "primary_color": "#4f46e5",
    "accent_color": "#0ea5a4",
    # printed bill / receipt header
    "address": "",
    "phone": "",
    "email": "",
    "license_no": "",
    "receipt_footer": "Thank you! Get well soon.",
}


class SiteSettings(models.Model):
    """Single-row (singleton) branding settings editable by the admin."""
    PRINT_THEMES = (
        ("classic", "Classic Letterhead"),
        ("modern", "Modern Colour Band"),
        ("elegant", "Elegant Serif"),
        ("minimal", "Clean Minimal"),
        ("letterhead", "My pre-printed letterhead (blank top)"),
    )

    brand_name = models.CharField(max_length=60, default=SITE_DEFAULTS["brand_name"])
    brand_tagline = models.CharField(max_length=80, blank=True, default=SITE_DEFAULTS["brand_tagline"])
    logo_text = models.CharField(max_length=2, default=SITE_DEFAULTS["logo_text"],
                                 help_text="1–2 letters shown in the logo badge when no image is set")
    logo_image = models.ImageField(upload_to="branding/", blank=True, null=True)
    primary_color = models.CharField(max_length=7, default=SITE_DEFAULTS["primary_color"],
                                     help_text="Main theme colour (hex, e.g. #4f46e5)")
    accent_color = models.CharField(max_length=7, default=SITE_DEFAULTS["accent_color"],
                                    help_text="Secondary/gradient colour (hex)")

    # printed bill / receipt header details
    address = models.CharField(max_length=255, blank=True, default="",
                               help_text="Shown on printed bills & reports")
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.CharField(max_length=120, blank=True, default="")
    license_no = models.CharField(max_length=80, blank=True, default="",
                                  help_text="Drug sale licence / registration no. (optional)")
    receipt_footer = models.CharField(max_length=200, blank=True,
                                      default=SITE_DEFAULTS["receipt_footer"],
                                      help_text="Message printed at the bottom of bills")

    # which design to use for printed reports / bills
    print_theme = models.CharField(max_length=20, choices=PRINT_THEMES, default="classic",
                                   help_text="Design used for printed lab reports, bills & receipts")

    # which business modules this install uses; null = all modules on
    enabled_modules = models.JSONField(null=True, blank=True, default=None,
                                       help_text="List of enabled module keys (null = all)")

    hospital = models.OneToOneField('saas.Hospital', on_delete=models.CASCADE, related_name='site_settings', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        if self.hospital:
            return f"Site settings ({self.brand_name} - {self.hospital.name})"
        return f"Site settings ({self.brand_name})"

    def save(self, *args, **kwargs):
        if not self.hospital:
            self.pk = 1  # enforce singleton only for global settings
        super().save(*args, **kwargs)

    def reset_to_defaults(self):
        for field, value in SITE_DEFAULTS.items():
            setattr(self, field, value)
        if self.logo_image:
            self.logo_image.delete(save=False)
        self.logo_image = None
        self.save()

    @classmethod
    def load(cls):
        from saas.utils import get_current_hospital
        hospital = get_current_hospital()
        if hospital:
            obj, _ = cls.objects.get_or_create(hospital=hospital)
            return obj
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Organization(models.Model):
	name = models.CharField(max_length=120, unique=True)

	def __str__(self):
		return self.name

class UserProfile(models.Model):
	user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
	organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, blank=True)

	def __str__(self):
		return f"{self.user} → {self.organization or 'No Org'}"

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
	if created:
		UserProfile.objects.create(user=instance)