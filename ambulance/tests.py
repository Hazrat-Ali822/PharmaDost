"""Ambulance fleet, dispatch and charging.

    python manage.py test ambulance --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from accounts.models import User
from ambulance.models import Ambulance, AmbulanceDriver, AmbulanceTrip
from ambulance.services import cancel_trip, complete_trip, dispatch_trip
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=365)


class AmbulanceBase(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-amb', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.recep = User.objects.create_user(email='r@a.com', password='pw',
                                              role='RECEPTIONIST', hospital=self.h)
        self.driver = AmbulanceDriver.objects.create(full_name='Karim', phone='03001234567',
                                                     hospital=self.h)
        self.van = Ambulance.objects.create(
            registration_no='LEA-1234', label='Ambulance 1', driver=self.driver,
            base_charge=Decimal('1500'), per_km_charge=Decimal('50'),
            waiting_charge_per_hour=Decimal('200'), cost_price=Decimal('600'),
            hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M',
                                              age_years=40, hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _trip(self, **kw):
        data = dict(ambulance=self.van, patient=self.patient, from_location='Village',
                    to_location='Hospital', hospital=self.h)
        data.update(kw)
        return AmbulanceTrip(**data)


class DispatchTest(AmbulanceBase):

    def test_dispatch_freezes_the_vehicles_rates_onto_the_trip(self):
        trip = dispatch_trip(self._trip(), user=self.admin)
        self.assertEqual(trip.base_charge, Decimal('1500'))
        self.assertEqual(trip.per_km_charge, Decimal('50'))
        self.assertEqual(trip.cost_price, Decimal('600'))

    def test_repricing_the_vehicle_does_not_rewrite_an_old_trip(self):
        """The rule every frozen-charge field in this codebase follows."""
        trip = dispatch_trip(self._trip(), user=self.admin)
        self.van.base_charge = Decimal('9999')
        self.van.save()
        trip.refresh_from_db()
        self.assertEqual(trip.base_charge, Decimal('1500'))

    def test_a_rate_typed_on_the_trip_wins(self):
        trip = dispatch_trip(self._trip(base_charge=Decimal('3000')), user=self.admin)
        self.assertEqual(trip.base_charge, Decimal('3000'))
        self.assertEqual(trip.per_km_charge, Decimal('50'))   # the untouched one still filled

    def test_dispatch_takes_the_vehicle_off_the_free_list(self):
        dispatch_trip(self._trip(), user=self.admin)
        self.van.refresh_from_db()
        self.assertEqual(self.van.status, Ambulance.STATUS_ON_TRIP)
        self.assertFalse(self.van.is_free)

    def test_the_same_ambulance_cannot_be_sent_twice(self):
        """Unlike a double-booked bed there is no second van parked outside."""
        dispatch_trip(self._trip(), user=self.admin)
        with self.assertRaises(ValidationError):
            dispatch_trip(self._trip(), user=self.admin)

    def test_an_out_of_service_vehicle_is_refused(self):
        self.van.is_active = False
        self.van.save()
        with self.assertRaises(ValidationError):
            dispatch_trip(self._trip(), user=self.admin)

    def test_the_vehicles_usual_driver_is_used_when_none_is_named(self):
        trip = dispatch_trip(self._trip(), user=self.admin)
        self.assertEqual(trip.driver, self.driver)


class ChargeTest(AmbulanceBase):

    def test_the_bill_is_call_out_plus_distance_plus_waiting(self):
        trip = dispatch_trip(self._trip(distance_km=Decimal('12'),
                                        waiting_hours=Decimal('1.5')), user=self.admin)
        # 1500 + 12*50 + 1.5*200 = 1500 + 600 + 300
        self.assertEqual(trip.total_charge, Decimal('2400.0'))

    def test_each_part_is_its_own_bill_line_and_zero_parts_are_omitted(self):
        trip = dispatch_trip(self._trip(distance_km=Decimal('10')), user=self.admin)
        descs = [d for d, _a in trip.charge_lines()]
        self.assertIn('Ambulance: Emergency pick-up', descs)
        self.assertTrue(any(d.startswith('Ambulance Distance:') for d in descs))
        self.assertFalse(any(d.startswith('Ambulance Waiting:') for d in descs))

    def test_completing_raises_an_invoice_and_frees_the_vehicle(self):
        trip = dispatch_trip(self._trip(distance_km=Decimal('10')), user=self.admin)
        invoice = complete_trip(trip, user=self.admin)
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.total, Decimal('2000.00'))     # 1500 + 500
        trip.refresh_from_db()
        self.van.refresh_from_db()
        self.assertEqual(trip.invoice, invoice)
        self.assertEqual(self.van.status, Ambulance.STATUS_AVAILABLE)

    def test_a_free_service_produces_no_invoice_and_that_is_not_a_failure(self):
        """A hospital that does not charge for ambulance runs, or a transfer it
        caused itself."""
        free_van = Ambulance.objects.create(registration_no='LEA-9', hospital=self.h)
        trip = dispatch_trip(self._trip(ambulance=free_van), user=self.admin)
        self.assertIsNone(complete_trip(trip, user=self.admin))
        trip.refresh_from_db()
        self.assertEqual(trip.status, AmbulanceTrip.STATUS_COMPLETED)

    def test_a_trip_with_no_patient_is_recorded_but_not_invoiced(self):
        """A body transfer must not put the deceased in the patient register."""
        trip = dispatch_trip(self._trip(patient=None, contact_name='Family',
                                        trip_type='BODY'), user=self.admin)
        self.assertIsNone(complete_trip(trip, user=self.admin))
        trip.refresh_from_db()
        self.assertEqual(trip.status, AmbulanceTrip.STATUS_COMPLETED)
        self.assertEqual(trip.who, 'Family')

    def test_completing_twice_is_refused_so_a_run_cannot_bill_twice(self):
        trip = dispatch_trip(self._trip(), user=self.admin)
        complete_trip(trip, user=self.admin)
        with self.assertRaises(ValidationError):
            complete_trip(trip, user=self.admin)

    def test_the_invoice_lines_classify_as_ambulance_revenue(self):
        from billing.revenue import AMBULANCE, classify
        trip = dispatch_trip(self._trip(distance_km=Decimal('10'),
                                        waiting_hours=Decimal('2')), user=self.admin)
        for desc, _amount in trip.charge_lines():
            self.assertEqual(classify(desc), AMBULANCE, desc)


class CancelTest(AmbulanceBase):

    def test_cancelling_needs_a_reason(self):
        trip = dispatch_trip(self._trip(), user=self.admin)
        with self.assertRaises(ValidationError):
            cancel_trip(trip, reason='  ', user=self.admin)

    def test_cancelling_frees_the_vehicle(self):
        trip = dispatch_trip(self._trip(), user=self.admin)
        cancel_trip(trip, reason='Family arranged their own transport', user=self.admin)
        self.van.refresh_from_db()
        self.assertEqual(self.van.status, Ambulance.STATUS_AVAILABLE)

    def test_a_completed_run_cannot_be_cancelled(self):
        """The van went. That is then a billing decision, not a dispatch one."""
        trip = dispatch_trip(self._trip(), user=self.admin)
        complete_trip(trip, user=self.admin)
        with self.assertRaises(ValidationError):
            cancel_trip(trip, reason='mistake', user=self.admin)

    def test_a_vehicle_with_another_open_trip_stays_out(self):
        """Sent out twice by mistake — finishing one must not put it back on the
        board while the other run is still going."""
        t1 = dispatch_trip(self._trip(), user=self.admin)
        t2 = self._trip()
        AmbulanceTrip.objects.bulk_create([t2])            # bypass the guard on purpose
        cancel_trip(t1, reason='stood down', user=self.admin)
        self.van.refresh_from_db()
        self.assertEqual(self.van.status, Ambulance.STATUS_ON_TRIP)


class AmbulanceScreensTest(AmbulanceBase):

    def test_reception_can_reach_the_board_and_book_a_trip(self):
        c = Client(); c.login(email='r@a.com', password='pw')
        self.assertEqual(c.get('/ambulance/').status_code, 200)
        r = c.post('/ambulance/trips/new/', {
            'ambulance': self.van.pk, 'trip_type': 'EMERGENCY',
            'patient': self.patient.pk, 'from_location': 'Village',
            'to_location': 'Hospital', 'called_at': '2026-08-17 10:00:00',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AmbulanceTrip.objects.count(), 1)

    def test_reception_cannot_edit_the_fleet(self):
        c = Client(); c.login(email='r@a.com', password='pw')
        self.assertEqual(c.get('/ambulance/fleet/').status_code, 403)

    def test_admin_can_edit_the_fleet(self):
        c = Client(); c.login(email='a@a.com', password='pw')
        self.assertEqual(c.get('/ambulance/fleet/').status_code, 200)

    def test_a_trip_needs_a_patient_or_at_least_a_caller(self):
        """Otherwise it is a record nobody can look up later."""
        from ambulance.forms import AmbulanceTripForm
        form = AmbulanceTripForm({'ambulance': self.van.pk, 'trip_type': 'OTHER',
                                  'from_location': 'A', 'to_location': 'B',
                                  'called_at': '2026-08-17 10:00:00'}, user=self.recep)
        self.assertFalse(form.is_valid())


class AmbulanceTenantTest(TestCase):

    def setUp(self):
        self.a = Hospital.objects.create(name='A', slug='a-amb', expiry_date=_future())
        self.b = Hospital.objects.create(name='B', slug='b-amb', expiry_date=_future())
        self.admin_a = User.objects.create_user(email='aa@a.com', password='pw',
                                                role='ADMIN', hospital=self.a)
        self.van_b = Ambulance.all_objects.create(registration_no='THEIRS-1', hospital=self.b)
        self.driver_b = AmbulanceDriver.all_objects.create(full_name='Their Driver',
                                                           hospital=self.b)

    def tearDown(self):
        clear_current_hospital()

    def test_the_fleet_screen_never_shows_another_hospitals_vehicle(self):
        c = Client(); c.login(email='aa@a.com', password='pw')
        body = c.get('/ambulance/fleet/').content.decode()
        self.assertNotIn('THEIRS-1', body)
        self.assertNotIn('Their Driver', body)

    def test_a_post_naming_another_hospitals_ambulance_is_rejected(self):
        """The dropdown is scoped in __init__, and ModelChoiceField validates the
        posted id against that same queryset."""
        from ambulance.forms import AmbulanceTripForm
        form = AmbulanceTripForm({'ambulance': self.van_b.pk, 'trip_type': 'OTHER',
                                  'contact_name': 'X', 'from_location': 'A',
                                  'to_location': 'B', 'called_at': '2026-08-17 10:00:00'},
                                 user=self.admin_a)
        self.assertFalse(form.is_valid())
        self.assertIn('ambulance', form.errors)


class AmbulanceProfitTest(AmbulanceBase):
    """The trip's own cost sits next to the revenue it earned."""

    def test_the_profit_report_shows_ambulance_with_a_real_cost(self):
        from datetime import date

        from reports.utils import module_profit_data

        trip = dispatch_trip(self._trip(distance_km=Decimal('10')), user=self.admin)
        complete_trip(trip, user=self.admin)

        today = date.today()
        rows, totals = module_profit_data(today, today)
        row = next(r for r in rows if r['key'] == 'AMBULANCE')
        self.assertEqual(row['revenue'], Decimal('2000.00'))    # 1500 + 10*50
        self.assertEqual(row['cost'], Decimal('600.00'))
        self.assertTrue(row['cost_tracked'])
        self.assertEqual(row['profit'], Decimal('1400.00'))

    def test_a_fleet_with_no_cost_recorded_says_so_rather_than_100_percent(self):
        from datetime import date

        from reports.utils import module_profit_data

        self.van.cost_price = Decimal('0.00')
        self.van.save()
        trip = dispatch_trip(self._trip(), user=self.admin)
        complete_trip(trip, user=self.admin)

        today = date.today()
        rows, _totals = module_profit_data(today, today)
        row = next(r for r in rows if r['key'] == 'AMBULANCE')
        self.assertEqual(row['cost'], Decimal('0.00'))
        self.assertIn('No cost entered yet', row['note'])
