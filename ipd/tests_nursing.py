"""Nursing / ward management — duty roster, patient allocation, my duties.

The roster and allocation are *management* actions gated on `ward_manage`
(Ward In-charge / Admin); a plain nurse can view the roster and their own
duties but cannot build them. These tests hold that line and the happy path.
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor
from ipd.models import (Ward, Bed, Admission, NurseShift, PatientAllocation,
                        VitalsObservation, FluidBalanceEntry, compute_mews, fluid_totals)


class NursingWardManagementTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        self.admin = User.objects.create_user(email='admin@a.com', password='pw',
                                               role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(email='nurse@a.com', password='pw',
                                               role='NURSE', hospital=self.h, first_name='Sana')
        self.doc = Doctor.objects.create(full_name='Dr Ali')
        self.patient = Patient.objects.create(full_name='Bilal', gender='M',
                                              age_years=40, hospital=self.h)
        self.ward = Ward.objects.create(name='Male Ward', ward_type='General Male',
                                        daily_rate=1000, hospital=self.h)
        self.bed = Bed.objects.create(bed_number='B1', ward=self.ward,
                                      status='Occupied', hospital=self.h)
        self.adm = Admission.objects.create(patient=self.patient, bed=self.bed,
                                            admission_reason='obs', attending_doctor=self.doc,
                                            hospital=self.h)
        self.today = timezone.localdate().isoformat()

    def _admin(self):
        c = Client(); c.login(email='admin@a.com', password='pw'); return c

    def _nurse(self):
        c = Client(); c.login(email='nurse@a.com', password='pw'); return c

    def test_incharge_rosters_a_nurse_then_allocates_a_patient(self):
        c = self._admin()
        self.assertEqual(c.get(f'/ipd/roster/?ward={self.ward.pk}').status_code, 200)

        c.post('/ipd/roster/add/', {'ward': self.ward.pk, 'nurse': self.nurse.pk,
                                    'date': self.today, 'shift': 'MORNING', 'duty': 'STAFF'})
        self.assertEqual(NurseShift.objects.count(), 1)

        page = c.get(f'/ipd/allocation/?ward={self.ward.pk}&date={self.today}&shift=MORNING')
        self.assertContains(page, 'Bilal')
        self.assertContains(page, 'Sana')

        c.post(f'/ipd/allocation/?ward={self.ward.pk}&date={self.today}&shift=MORNING',
               {f'alloc_{self.adm.pk}': self.nurse.pk})
        self.assertEqual(PatientAllocation.objects.count(), 1)
        self.assertEqual(PatientAllocation.objects.first().nurse, self.nurse)

    def test_roster_is_idempotent_on_the_unique_shift(self):
        c = self._admin()
        for _ in range(2):
            c.post('/ipd/roster/add/', {'ward': self.ward.pk, 'nurse': self.nurse.pk,
                                        'date': self.today, 'shift': 'MORNING', 'duty': 'STAFF'})
        # (nurse, date, shift) is unique — a second add updates, never duplicates.
        self.assertEqual(NurseShift.objects.filter(nurse=self.nurse, shift='MORNING').count(), 1)

    def test_nurse_sees_own_duties_but_cannot_manage(self):
        # give the nurse a shift + an allocated patient today
        NurseShift.objects.create(nurse=self.nurse, ward=self.ward, date=timezone.localdate(),
                                  shift='MORNING', hospital=self.h)
        PatientAllocation.objects.create(admission=self.adm, nurse=self.nurse,
                                         date=timezone.localdate(), shift='MORNING', hospital=self.h)
        c = self._nurse()
        duties = c.get('/ipd/my-duties/')
        self.assertContains(duties, 'Bilal')
        # can view the roster...
        self.assertEqual(c.get('/ipd/roster/').status_code, 200)
        # ...but building it and allocating patients is ward_manage — denied.
        self.assertEqual(c.get('/ipd/allocation/').status_code, 403)
        self.assertEqual(c.post('/ipd/roster/add/', {'ward': self.ward.pk, 'nurse': self.nurse.pk,
                                'date': self.today, 'shift': 'NIGHT'}).status_code, 403)
        self.assertFalse(NurseShift.objects.filter(shift='NIGHT').exists())


class MewsScoringTest(TestCase):
    def test_normal_vitals_score_green(self):
        m = compute_mews(temperature_f=98.6, pulse=78, respiratory_rate=16,
                         systolic_bp=120, avpu='A')
        self.assertEqual(m['score'], 0)
        self.assertEqual(m['band'], 'GREEN')
        self.assertTrue(m['complete'])

    def test_deteriorating_vitals_score_red(self):
        # fast RR (3) + low BP (2) + tachycardia + febrile → high total, RED
        m = compute_mews(temperature_f=103.0, pulse=132, respiratory_rate=32,
                         systolic_bp=85, avpu='V')
        self.assertGreaterEqual(m['score'], 4)
        self.assertEqual(m['band'], 'RED')

    def test_a_single_worst_parameter_is_red(self):
        # RR 32 alone scores 3 → RED even if everything else is normal
        m = compute_mews(temperature_f=98.6, pulse=80, respiratory_rate=32,
                         systolic_bp=120, avpu='A')
        self.assertEqual(m['highest'], 3)
        self.assertEqual(m['band'], 'RED')

    def test_partial_set_is_marked_incomplete(self):
        m = compute_mews(temperature_f=None, pulse=80, respiratory_rate=None,
                         systolic_bp=120, avpu='A')
        self.assertFalse(m['complete'])


class VitalsAndFluidTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))
        self.nurse = User.objects.create_user(email='n@a.com', password='pw',
                                               role='NURSE', hospital=self.h)
        self.doc = Doctor.objects.create(full_name='Dr X')
        self.patient = Patient.objects.create(full_name='Zara', gender='F',
                                              age_years=30, hospital=self.h)
        self.ward = Ward.objects.create(name='W', ward_type='General Female',
                                        daily_rate=500, hospital=self.h)
        self.bed = Bed.objects.create(bed_number='1', ward=self.ward,
                                      status='Occupied', hospital=self.h)
        self.adm = Admission.objects.create(patient=self.patient, bed=self.bed,
                                            admission_reason='x', attending_doctor=self.doc,
                                            hospital=self.h)
        self.c = Client(); self.c.login(email='n@a.com', password='pw')

    def test_nurse_records_vitals_and_board_shows_mews(self):
        r = self.c.post(f'/ipd/{self.adm.pk}/vitals/', {
            'taken_at': timezone.now().strftime('%Y-%m-%dT%H:%M'),
            'temperature': '103', 'pulse': '132', 'respiratory_rate': '32',
            'systolic_bp': '85', 'diastolic_bp': '60', 'spo2': '90',
            'consciousness': 'V', 'pain_score': '6', 'blood_glucose': '',
            'notes': 'unwell',
        })
        self.assertEqual(VitalsObservation.objects.count(), 1)
        obs = VitalsObservation.objects.first()
        self.assertEqual(obs.taken_by, self.nurse)
        self.assertEqual(obs.mews['band'], 'RED')
        # the board surfaces the patient with the red score
        board = self.c.get('/ipd/board/')
        self.assertContains(board, 'Zara')
        self.assertContains(board, 'RED')

    def test_empty_vitals_are_rejected(self):
        self.c.post(f'/ipd/{self.adm.pk}/vitals/', {
            'taken_at': timezone.now().strftime('%Y-%m-%dT%H:%M'), 'consciousness': 'A'})
        self.assertEqual(VitalsObservation.objects.count(), 0)

    def test_fluid_balance_totals(self):
        FluidBalanceEntry.objects.create(admission=self.adm, direction='IN',
                                         kind='IV fluid', volume_ml=1000, hospital=self.h)
        FluidBalanceEntry.objects.create(admission=self.adm, direction='OUT',
                                         kind='Urine', volume_ml=600, hospital=self.h)
        bal = fluid_totals(self.adm, timezone.localdate())
        self.assertEqual(bal['intake'], 1000)
        self.assertEqual(bal['output'], 600)
        self.assertEqual(bal['balance'], 400)
