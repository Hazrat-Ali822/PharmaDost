"""Modern-pharmacy additions: allergy/duplicate screening, auto-PO, analytics,
alert commands, dosage labels."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.core.management import call_command

from accounts.models import User
from patients.models import Patient
from opd.models import Doctor, Appointment
from suppliers.models import Supplier
from inventory.models import Medicine, PurchaseRequest
from inventory.safety import screen_medicines
from inventory.services import inventory_analytics
from prescriptions.models import Prescription, PrescriptionItem


def _exp():
    return date.today() + timedelta(days=365)


class SafetyScreenTest(TestCase):
    def test_allergy_and_duplicate(self):
        p = Patient.objects.create(full_name='A', gender='M', allergies='Amoxicillin, Sulfa')
        m1 = Medicine.objects.create(name='Amoxil', brand='X', generic_name='Amoxicillin',
                                     price=Decimal('10'), expiry_date=_exp())
        m2 = Medicine.objects.create(name='Brufen', brand='Y', generic_name='Ibuprofen',
                                     price=Decimal('10'), expiry_date=_exp())
        m3 = Medicine.objects.create(name='Ibugesic', brand='Z', generic_name='Ibuprofen',
                                     price=Decimal('10'), expiry_date=_exp())
        warns = screen_medicines(p, [m1, m2, m3])
        self.assertTrue(any('ALLERGY' in w and 'Amoxil' in w for w in warns))
        self.assertTrue(any('DUPLICATE' in w for w in warns))

    def test_no_false_positive(self):
        p = Patient.objects.create(full_name='B', gender='M', allergies='')
        m = Medicine.objects.create(name='Panadol', generic_name='Paracetamol',
                                    price=Decimal('5'), expiry_date=_exp())
        self.assertEqual(screen_medicines(p, [m]), [])


class AutoPOTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN')
        self.sup = Supplier.objects.create(name='S', phone='0')
        self.med = Medicine.objects.create(name='M', brand='B', price=Decimal('10'),
                                           reorder_level=50, expiry_date=_exp(), supplier=self.sup)
        self.med.add_stock(10, expiry_date=date.today() + timedelta(days=200), cost_price=Decimal('6'))
        from sales.models import Sale, SaleItem
        s = Sale.objects.create(cashier=self.admin)
        SaleItem.objects.create(sale=s, medicine=self.med, unit_price=Decimal('10'), quantity=60)

    def test_reorder_to_po_creates_draft(self):
        c = Client(); c.force_login(self.admin)
        r = c.post(reverse('reorder_to_po'), {'days': 30})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(PurchaseRequest.objects.count(), 1)
        pr = PurchaseRequest.objects.get()
        self.assertEqual(pr.supplier, self.sup)
        self.assertEqual(pr.items.count(), 1)
        item = pr.items.first()
        self.assertGreater(item.quantity, 0)
        self.assertEqual(item.cost_price, Decimal('6.00'))


class AnalyticsTest(TestCase):
    def test_dead_stock_and_movers(self):
        u = User.objects.create_user(email='c@c.com', password='pw', role='PHARMACIST')
        sold = Medicine.objects.create(name='Sold', generic_name='x', price=Decimal('10'), expiry_date=_exp())
        sold.add_stock(5, expiry_date=date.today() + timedelta(days=200))
        dead = Medicine.objects.create(name='Dead', generic_name='y', price=Decimal('10'), expiry_date=_exp())
        dead.add_stock(20, expiry_date=date.today() + timedelta(days=200))
        from sales.models import Sale, SaleItem
        s = Sale.objects.create(cashier=u)
        SaleItem.objects.create(sale=s, medicine=sold, unit_price=Decimal('10'), quantity=3)
        data = inventory_analytics(days=90)
        self.assertTrue(any(r['medicine'].id == dead.id for r in data['dead_stock']))
        self.assertTrue(any(r['medicine'].id == sold.id for r in data['top_movers']))
        # analytics + reorder pages render without template errors
        c = Client(); c.force_login(User.objects.create_user(email='v@v.com', password='pw', role='ADMIN'))
        self.assertEqual(c.get(reverse('inventory_analytics')).status_code, 200)
        self.assertEqual(c.get(reverse('reorder_report')).status_code, 200)
        self.assertEqual(c.get(reverse('expiry_report')).status_code, 200)


class CommandsRunTest(TestCase):
    def test_alert_commands_run(self):
        # a low-stock + near-expiry medicine so the commands have something to do
        m = Medicine.objects.create(name='Low', price=Decimal('10'), reorder_level=100,
                                    quantity=1, expiry_date=date.today() + timedelta(days=5))
        m.add_stock(1, expiry_date=date.today() + timedelta(days=5))
        call_command('low_stock_alert')
        call_command('expiry_alert')
        call_command('reconcile_stock')


class LabelsRenderTest(TestCase):
    def test_labels_render(self):
        admin = User.objects.create_user(email='ad@ad.com', password='pw', role='ADMIN')
        patient = Patient.objects.create(full_name='P', mrn='M1', gender='M')
        doctor = Doctor.objects.create(full_name='Dr X', opd_fee=Decimal('100'))
        appt = Appointment.objects.create(patient=patient, doctor=doctor)
        rx = Prescription.objects.create(appointment=appt, diagnosis='Fever')
        med = Medicine.objects.create(name='Panadol', price=Decimal('5'), expiry_date=_exp())
        PrescriptionItem.objects.create(prescription=rx, medicine=med, dosage='1+0+1',
                                        duration_days=5, instructions='After meal')
        c = Client(); c.force_login(admin)
        resp = c.get(reverse('prescription_labels', args=[rx.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Panadol')
        self.assertContains(resp, '1+0+1')


class HomePageRoutingTest(TestCase):
    """The home page ('/') must never 403 a logged-in user who lacks pharmacy
    access — it should route them to their own role dashboard instead."""
    def test_non_pharmacy_user_is_redirected_not_forbidden(self):
        recep = User.objects.create_user(email='r@r.com', password='pw', role='RECEPTIONIST')
        c = Client(); c.force_login(recep)
        resp = c.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 302)               # redirected, not 403
        self.assertNotEqual(resp.status_code, 403)

    def test_admin_sees_dashboard(self):
        admin = User.objects.create_user(email='ad2@ad.com', password='pw', role='ADMIN')
        c = Client(); c.force_login(admin)
        self.assertEqual(c.get(reverse('dashboard')).status_code, 200)
