from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


# Defaults used for the initial values AND the "Reset to default" action.
SITE_DEFAULTS = {
    "brand_name": "Sehatyar",
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
    "currency_symbol": "Rs",
}


def current_currency():
    """The active tenant's currency symbol (falls back to 'Rs').

    For flash messages and other Python-side strings where there is no `branding`
    template variable to hand. It runs a query, so keep it to one-off actions —
    never inside a loop or a hot path.
    """
    try:
        return SiteSettings.load().currency_symbol or "Rs"
    except Exception:
        return "Rs"


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

    # the app's default light/dark mode for this hospital (a device can still toggle)
    default_theme = models.CharField(max_length=10,
                                     choices=(("light", "Light"), ("dark", "Dark"),
                                              ("auto", "Follow the device")),
                                     default="light",
                                     help_text="Light or dark by default for everyone (each device can still switch)")
    # send a WhatsApp button on bills (uses the free wa.me link — no gateway needed)
    whatsapp_enabled = models.BooleanField(
        default=True, help_text="Show a 'Send on WhatsApp' button on patient bills")
    # print a scannable QR of the bill summary on the printed bill (needs no internet)
    show_bill_qr = models.BooleanField(
        default=True, help_text="Print a scannable QR of the bill summary on printed bills")

    # the currency symbol shown before every amount across the app and on prints
    currency_symbol = models.CharField(
        max_length=8, default="Rs",
        help_text="Shown before every amount, e.g. Rs, ₨, PKR, $, ﷼. Just the symbol/text.")

    # Bill maths. All default to a no-op (0% / no rounding) so behaviour is
    # unchanged until an admin opts in. Applied in sales.services.create_sale and
    # billing.services (service/OPD/discharge invoices).
    BILL_ROUNDING_CHOICES = (
        ("none", "No rounding"),
        ("1", "Nearest 1"),
        ("5", "Nearest 5"),
        ("10", "Nearest 10"),
    )
    default_tax_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Tax/GST added to every bill, as a %. 0 = no tax.")
    default_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="A standing discount taken off every pharmacy sale, as a %. "
                  "0 = none. A cashier can still override it on the counter.")
    bill_rounding = models.CharField(
        max_length=8, choices=BILL_ROUNDING_CHOICES, default="none",
        help_text="Round the final payable to the nearest 1/5/10 (handy where small "
                  "coins aren't used).")

    # show the prescribing doctor's name to pharmacy/POS staff (on the pending-Rx loader)
    show_doctor_to_pharmacy = models.BooleanField(
        default=True,
        help_text="Show the prescribing doctor's name to pharmacy staff on the POS pending-prescription list")

    # which business modules this install uses; null = all modules on
    enabled_modules = models.JSONField(null=True, blank=True, default=None,
                                       help_text="List of enabled module keys (null = all)")

    # Patient MRN numbering. The counter lives here because this row is already
    # the per-hospital singleton with a hospital-less fallback, so one lock covers
    # both a SaaS tenant and a single-site desktop install.
    mrn_prefix = models.CharField(
        max_length=6, blank=True, default="",
        help_text="Letters in front of the patient number, e.g. SGH in SGH-000001. "
                  "Leave blank to derive it from the brand name.")
    mrn_last_number = models.PositiveIntegerField(
        default=0, help_text="Last patient number issued. Raise it to skip ahead; "
                             "lowering it risks colliding with existing MRNs.")

    # Invoice (service/OPD bill) numbering — its own per-hospital counter, same
    # locked-row pattern as the MRN counter above. Pharmacy POS receipts keep their
    # own "Sale #id"; this is the accounting document number.
    invoice_prefix = models.CharField(
        max_length=8, blank=True, default="INV",
        help_text="Letters in front of the invoice number, e.g. INV in INV-2026-00001.")
    invoice_year_in_number = models.BooleanField(
        default=True, help_text="Put the year in the invoice number (INV-2026-00001). "
                                "Off gives INV-00001.")
    invoice_last_number = models.PositiveIntegerField(
        default=0, help_text="Last invoice number issued. With the year switched on the "
                             "count restarts each calendar year.")
    invoice_number_year = models.PositiveIntegerField(
        default=0, help_text="The year the counter is currently running in (used to "
                             "restart numbering each year). Managed automatically.")

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
        # NOTE: we deliberately do NOT force pk=1 for the global row anymore.
        # Inserting an explicit primary key on PostgreSQL leaves the id sequence
        # behind, so the next hospital's settings row would collide on id=1
        # ("duplicate key ... already exists"). Letting every row take its id from
        # the sequence keeps the global singleton at whatever id it already has and
        # never desyncs the sequence. `load()` guarantees there is only one global
        # (hospital-less) row.
        super().save(*args, **kwargs)

    def tax_on(self, base):
        """The tax amount on `base` (an already-discounted subtotal). 0 when off."""
        from decimal import Decimal
        pct = Decimal(self.default_tax_percent or 0)
        if pct <= 0:
            return Decimal("0.00")
        return (Decimal(str(base)) * pct / Decimal(100)).quantize(Decimal("0.01"))

    def round_total(self, amount):
        """Round the final payable to the configured step (nearest 1/5/10)."""
        from decimal import Decimal, ROUND_HALF_UP
        step = {"1": 1, "5": 5, "10": 10}.get(self.bill_rounding)
        amount = Decimal(str(amount))
        if not step:
            return amount
        step = Decimal(step)
        return (amount / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step

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
            obj = cls.objects.filter(hospital=hospital).first()
            if not obj:
                obj = cls.objects.create(
                    hospital=hospital,
                    brand_name=hospital.name,
                    brand_tagline=SITE_DEFAULTS["brand_tagline"],
                    logo_text=SITE_DEFAULTS["logo_text"],
                    primary_color=SITE_DEFAULTS["primary_color"],
                    accent_color=SITE_DEFAULTS["accent_color"],
                    address=SITE_DEFAULTS["address"],
                    phone=SITE_DEFAULTS["phone"],
                    email=SITE_DEFAULTS["email"],
                    license_no=SITE_DEFAULTS["license_no"],
                    receipt_footer=SITE_DEFAULTS["receipt_footer"],
                )
            return obj
        # global (hospital-less) singleton: reuse the existing hospital-less row
        # (historically pk=1), else create one via the id sequence
        obj = cls.objects.filter(hospital__isnull=True).order_by('id').first()
        if obj is None:
            obj = cls.objects.create()
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