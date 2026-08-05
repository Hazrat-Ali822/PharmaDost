"""Panel / Insurance / Sehat Card payers.

A Panel is an institutional payer — a private insurance company, a corporate
client, or the government Sehat Card (Sehat Sahulat Program) — that settles the
bills of the patients it covers. It is a per-tenant ledger, the same shape as the
`customers` khata: invoices billed to the panel are debits it owes, panel payments
are credits. The outstanding is computed from those, never stored, so it can never
drift (unlike the customer balance, which is a stored column with a reconcile job).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from saas.utils import TenantManager


class Panel(models.Model):
    INSURANCE = "INSURANCE"
    CORPORATE = "CORPORATE"
    SEHAT_CARD = "SEHAT_CARD"
    TYPE_CHOICES = (
        (INSURANCE, "Insurance Company"),
        (CORPORATE, "Corporate / Company Panel"),
        (SEHAT_CARD, "Govt Sehat Card"),
    )

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=15, choices=TYPE_CHOICES, default=INSURANCE)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True, db_index=True)
    address = models.TextField(blank=True)
    # The patient's own share, kept for reference/printing; billing collects the
    # co-pay through the invoice's `paid` field, so this is advisory in v1.
    copay_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class PanelPayment(models.Model):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("CHEQUE", "Cheque"),
        ("BANK", "Bank Transfer"),
        ("CARD", "Card"),
        ("OTHER", "Other"),
    )
    panel = models.ForeignKey(Panel, related_name="payments", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="BANK")
    reference = models.CharField(max_length=100, blank=True)   # cheque / transaction no
    notes = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    linked_invoice = models.ForeignKey(
        "billing.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="panel_payments"
    )
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = TenantManager()

    class Meta:
        ordering = ("-date", "-created_at")

    def __str__(self):
        return f"Payment {self.amount} from {self.panel}"
