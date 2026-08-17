from decimal import Decimal

from django.db import models
from django.utils import timezone
from saas.utils import TenantManager

class SurgeryCategory(models.Model):
    name = models.CharField(max_length=100)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Surgery Categories"

class SurgeryProcedure(models.Model):
    """The theatre price list: what one operation costs, itemised.

    A surgery used to bill as a single `standard_charge`, which meant the theatre
    time, the anaesthetist and the disposables were all folded into one number
    the patient could not read and the hospital could not analyse. Each part is
    now its own catalogue rate and its own line on the bill; all the new ones
    default to 0, so a hospital that never fills them in bills exactly as before.
    """
    name = models.CharField(max_length=150)
    category = models.ForeignKey(SurgeryCategory, on_delete=models.CASCADE, related_name='procedures')
    # The surgeon's / procedure fee. Kept under its original name because every
    # existing row, form and template already uses it.
    standard_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    ot_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                    help_text="Theatre / operating room charge")
    anesthesia_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                            help_text="Anaesthetist's fee")
    consumables_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                             help_text="Sutures, disposables, implants billed to the patient")
    # What the operation costs the hospital (kit, drugs, linen, sterilisation).
    # 0 means "not recorded", not "free" — the profit report says so rather than
    # claiming a 100% margin.
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                     help_text="Your own cost. Optional; used by the Profit by Module report.")
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    @property
    def total_charge(self):
        return (self.standard_charge + self.ot_charge
                + self.anesthesia_charge + self.consumables_charge)

    def __str__(self):
        return f"{self.name} (Rs {self.total_charge})"

class SurgeryRecord(models.Model):
    OUTCOME_CHOICES = [
        ('Successful', 'Successful'),
        ('Complications', 'Complications'),
        ('Failed', 'Failed'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='surgeries')
    admission = models.ForeignKey('ipd.Admission', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgeries')
    procedure = models.ForeignKey(SurgeryProcedure, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    lead_surgeon = models.ForeignKey('opd.Doctor', on_delete=models.CASCADE, related_name='surgeries_led')
    surgical_team = models.TextField(help_text="List other doctors, assistants, anesthetist, nurses etc.", blank=True)
    anesthesia_type = models.CharField(max_length=100, blank=True)
    operation_notes = models.TextField()
    outcome = models.CharField(max_length=50, choices=OUTCOME_CHOICES, default='Successful')

    # Charges are **frozen onto the record** at scheduling, prefilled from the
    # procedure but editable — a long or complicated operation uses the theatre
    # longer and costs more than the catalogue rate. Freezing also means
    # repricing the catalogue tomorrow cannot rewrite what a patient was billed
    # today, the same rule `SaleItem.cost_price` and `MedicationLog.unit_price`
    # already follow.
    surgeon_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    ot_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    anesthesia_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    consumables_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # The bill this operation raised (mirrors `Admission.discharge_invoice`).
    # The profit report needs revenue and cost on the *same* clock: revenue can
    # only be dated by the invoice, so without this link the cost was dated by
    # `start_time` and an operation scheduled for next week but billed today
    # landed its cost and its revenue in different periods.
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='surgeries')

    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    # (label, attribute, invoice-line prefix) — the single description of how a
    # surgery turns into bill lines. `billing.revenue` classifies on these
    # prefixes, so a new charge type needs adding in both places.
    CHARGE_PARTS = (
        ('Surgery', 'surgeon_charge', 'OT Surgery'),
        ('Theatre', 'ot_charge', 'OT Theatre'),
        ('Anaesthesia', 'anesthesia_charge', 'OT Anaesthesia'),
        ('Consumables', 'consumables_charge', 'OT Consumables'),
    )

    @property
    def total_charge(self):
        return (self.surgeon_charge + self.ot_charge
                + self.anesthesia_charge + self.consumables_charge)

    def charge_lines(self):
        """[(description, amount)] for the invoice — zero-value parts omitted."""
        lines = []
        for label, attr, prefix in self.CHARGE_PARTS:
            amount = getattr(self, attr) or Decimal('0.00')
            if amount <= 0:
                continue
            if attr == 'surgeon_charge':
                who = self.lead_surgeon.display_name if self.lead_surgeon else ''
                lines.append((f"{prefix}: {self.procedure.name} (Surgeon: {who})",
                              amount))
            else:
                lines.append((f"{prefix}: {self.procedure.name}", amount))
        return lines

    def __str__(self):
        return f"Surgery: {self.procedure.name} - {self.patient.full_name}"

class SurgeryRequest(models.Model):
    """A doctor's advice that a patient needs surgery. Lands in the OT / reception
    queue; on scheduling it becomes a real SurgeryRecord (bill raised)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Scheduled', 'Scheduled'),
        ('Cancelled', 'Cancelled'),
    ]
    URGENCY_CHOICES = [
        ('Elective', 'Elective (planned)'),
        ('Urgent', 'Urgent'),
        ('Emergency', 'Emergency'),
    ]
    patient = models.ForeignKey('patients.Patient', on_delete=models.CASCADE, related_name='surgery_requests')
    advised_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='surgery_advices')
    procedure = models.ForeignKey(SurgeryProcedure, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.TextField(help_text='Indication / reason for surgery')
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default='Elective')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    surgery = models.ForeignKey(SurgeryRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name='from_request')
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Surgery advice: {self.patient.full_name} ({self.status})"
