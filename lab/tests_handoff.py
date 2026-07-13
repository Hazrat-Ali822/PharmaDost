"""Doctor -> lab / sonographer hand-off: ordering must not 403 the doctor, and the
ordered work must show up on the lab tech / sonographer dashboard."""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from patients.models import Patient
from lab.models import TestCategory, LabTest, TestOrder
from imaging.models import ImagingStudy


class HandoffTest(TestCase):
    def setUp(self):
        self.doctor = User.objects.create_user(email='doc@t.com', password='pw', role='DOCTOR')
        self.labtech = User.objects.create_user(email='lab@t.com', password='pw', role='LABTECH')
        self.sono = User.objects.create_user(email='sono@t.com', password='pw', role='SONOGRAPHER')
        self.patient = Patient.objects.create(full_name='Test Patient', gender='M')
        cat = TestCategory.objects.create(name='Blood')
        self.test = LabTest.objects.create(category=cat, name='CBC', price=Decimal('300'))

    # ---- lab ----
    def test_doctor_orders_lab_no_403_and_labtech_sees_it(self):
        c = Client()
        c.force_login(self.doctor)
        r = c.post(reverse('lab:order_create'),
                   {'patient': self.patient.id, 'tests': [self.test.id]})
        # doctor must land on a page they can view (order detail), NOT a 403
        self.assertEqual(r.status_code, 302)
        self.assertIn(f'/lab/orders/{TestOrder.objects.get().id}', r.headers['Location'])
        follow = c.get(r.headers['Location'])
        self.assertEqual(follow.status_code, 200)   # doctor can open it, no permission error

        # the lab tech dashboard now shows the pending order
        lc = Client()
        lc.force_login(self.labtech)
        d = lc.get(reverse('user_mgmt:post_login_redirect'))
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.context['pending_count'], 1)
        self.assertIn(self.patient, [o.patient for o in d.context['pending_orders']])

    def test_labtech_ordering_goes_to_results(self):
        c = Client()
        c.force_login(self.labtech)
        r = c.post(reverse('lab:order_create'),
                   {'patient': self.patient.id, 'tests': [self.test.id]})
        self.assertEqual(r.status_code, 302)
        self.assertIn('edit-results', r.headers['Location'])  # lab tech goes straight to entry

    # ---- imaging ----
    def test_doctor_orders_imaging_no_403_and_sonographer_sees_it(self):
        c = Client()
        c.force_login(self.doctor)
        r = c.post(reverse('imaging:study_create'),
                   {'patient': self.patient.id, 'modality': 'ULTRASOUND',
                    'study_name': 'Abdomen US', 'price': '1500'})
        self.assertEqual(r.status_code, 302)
        study = ImagingStudy.objects.get()
        self.assertIn(f'/imaging/studies/{study.id}', r.headers['Location'])
        self.assertEqual(c.get(r.headers['Location']).status_code, 200)  # no 403

        sc = Client()
        sc.force_login(self.sono)
        d = sc.get(reverse('user_mgmt:post_login_redirect'))
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.context['pending_count'], 1)
