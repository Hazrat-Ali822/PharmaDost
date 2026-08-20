"""Emergency / Casualty department — walk-in and ambulance emergencies, triaged.

A casualty visit is registered fast (triage first, paperwork after), sorted by
triage priority so the sickest are seen first, and carries the medico-legal (MLC)
flag every government/police case needs. It is deliberately lighter than an OPD
appointment or an IPD admission: the emergency room needs to record who arrived,
how sick they are, and what happened to them, in seconds.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from opd.models import Doctor
from patients.models import Patient
from saas.utils import TenantManager


class EmergencyCase(models.Model):
    # Standard 4-level triage. RED first, BLACK (deceased/expectant) last.
    TRIAGE_CHOICES = [
        ('RED', 'Red — Immediate (resuscitation)'),
        ('YELLOW', 'Yellow — Urgent'),
        ('GREEN', 'Green — Non-urgent'),
        ('BLACK', 'Black — Deceased / expectant'),
    ]
    TRIAGE_ORDER = {'RED': 0, 'YELLOW': 1, 'GREEN': 2, 'BLACK': 3}

    ARRIVAL_CHOICES = [
        ('WALKIN', 'Walk-in'),
        ('AMBULANCE', 'Ambulance'),
        ('POLICE', 'Police'),
        ('REFERRED', 'Referred'),
        ('OTHER', 'Other'),
    ]
    DISPOSITION_CHOICES = [
        ('WAITING', 'Waiting for doctor'),
        ('IN_TREATMENT', 'In treatment'),
        ('ADMITTED', 'Admitted (IPD)'),
        ('DISCHARGED', 'Discharged'),
        ('REFERRED', 'Referred out'),
        ('LAMA', 'Left against medical advice'),
        ('EXPIRED', 'Expired'),
    ]
    ACTIVE_DISPOSITIONS = ('WAITING', 'IN_TREATMENT')

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='emergency_cases')
    arrival_time = models.DateTimeField(default=timezone.now)
    triage = models.CharField(max_length=8, choices=TRIAGE_CHOICES, default='YELLOW')
    chief_complaint = models.CharField(max_length=255, blank=True)
    mode_of_arrival = models.CharField(max_length=10, choices=ARRIVAL_CHOICES, default='WALKIN')
    brought_by = models.CharField(max_length=255, blank=True)

    # Medico-legal case: RTA, assault, poisoning, burns, anything police-reportable.
    is_mlc = models.BooleanField(default=False)
    mlc_no = models.CharField(max_length=50, blank=True)

    # Triage vitals — free text like the ward chart, so partial entries always save.
    pulse = models.CharField(max_length=20, blank=True)
    bp = models.CharField(max_length=20, blank=True)
    temp = models.CharField(max_length=20, blank=True)
    spo2 = models.CharField(max_length=20, blank=True)

    attending_doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='emergency_cases')
    disposition = models.CharField(max_length=14, choices=DISPOSITION_CHOICES, default='WAITING')
    disposition_notes = models.TextField(blank=True)
    disposed_at = models.DateTimeField(null=True, blank=True)

    invoice = models.ForeignKey('billing.Invoice', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+')
    # Dressings, cannula, oxygen, splint — frozen on the case rather than read
    # from a catalogue, because casualty consumables differ case by case. 0 =
    # not recorded; the profit report says so instead of claiming full margin.
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    hospital = models.ForeignKey('saas.Hospital', on_delete=models.CASCADE, null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f"Casualty #{self.pk} — {self.patient.full_name} ({self.get_triage_display()})"

    @property
    def is_open(self):
        return self.disposition in self.ACTIVE_DISPOSITIONS

    @property
    def triage_rank(self):
        return self.TRIAGE_ORDER.get(self.triage, 9)
