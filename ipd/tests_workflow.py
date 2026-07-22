"""Doctor -> reception/OT handoff: advise admission/surgery, then confirm from the queue."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Notification
from saas.models import Hospital
from patients.models import Patient
from opd.models import Doctor
from ipd.models import Ward, Bed, AdmissionRequest, Admission, MedicationLog
from ot.models import SurgeryCategory, SurgeryProcedure, SurgeryRequest, SurgeryRecord


class HandoffWorkflowTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=date.today() + timedelta(days=30))
        self.doc_user = User.objects.create_user(email='d@d.com', password='pw', role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=self.doc_user, full_name='Dr D', opd_fee=Decimal('100'))
        self.admin = User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='P One', gender='M', hospital=self.h)
        self.ward = Ward.objects.create(name='Gen', ward_type='General Male',
                                        daily_rate=Decimal('1000'), hospital=self.h)
        self.bed = Bed.objects.create(bed_number='B1', ward=self.ward, status='Available', hospital=self.h)
        cat = SurgeryCategory.objects.create(name='General', hospital=self.h)
        self.proc = SurgeryProcedure.objects.create(name='Appendectomy', category=cat,
                                                    standard_charge=Decimal('20000'), hospital=self.h)

    def test_admission_advise_then_confirm(self):
        c = Client(); c.force_login(self.doc_user)
        r = c.post(reverse('ipd:admission_advise', args=[self.patient.id]),
                   {'reason': 'Needs observation', 'preferred_ward': self.ward.id})
        self.assertEqual(r.status_code, 302)
        ar = AdmissionRequest.objects.get()
        self.assertEqual(ar.status, 'Pending')
        self.assertTrue(Notification.objects.filter(message__icontains='Admission advised').exists())

        # reception/admin confirms from the queue
        c2 = Client(); c2.force_login(self.admin)
        r2 = c2.post(reverse('ipd:admission_create') + f'?request_id={ar.id}', {
            'request_id': ar.id, 'patient': self.patient.id, 'bed': self.bed.id,
            'attending_doctor': self.doctor.id, 'admission_reason': 'Needs observation',
        })
        self.assertEqual(r2.status_code, 302)
        ar.refresh_from_db(); self.bed.refresh_from_db()
        self.assertEqual(ar.status, 'Admitted')
        self.assertIsNotNone(ar.admission_id)
        self.assertEqual(self.bed.status, 'Occupied')
        self.assertEqual(Admission.objects.count(), 1)

    def test_surgery_advise_then_schedule(self):
        c = Client(); c.force_login(self.doc_user)
        r = c.post(reverse('ot:surgery_advise', args=[self.patient.id]),
                   {'reason': 'Acute appendicitis', 'procedure': self.proc.id, 'urgency': 'Urgent'})
        self.assertEqual(r.status_code, 302)
        sr = SurgeryRequest.objects.get()
        self.assertEqual(sr.status, 'Pending')
        self.assertEqual(sr.urgency, 'Urgent')
        self.assertTrue(Notification.objects.filter(message__icontains='Surgery advised').exists())

        c2 = Client(); c2.force_login(self.admin)
        r2 = c2.post(reverse('ot:surgery_create') + f'?request_id={sr.id}', {
            'request_id': sr.id, 'patient': self.patient.id, 'procedure': self.proc.id,
            'start_time': '2026-07-20T10:00', 'lead_surgeon': self.doctor.id,
            'operation_notes': 'Standard appendectomy', 'outcome': 'Successful',
        })
        self.assertEqual(r2.status_code, 302)
        sr.refresh_from_db()
        self.assertEqual(sr.status, 'Scheduled')
        self.assertIsNotNone(sr.surgery_id)
        self.assertEqual(SurgeryRecord.objects.count(), 1)

    def test_pages_render(self):
        c = Client(); c.force_login(self.admin)
        # doctor advise forms
        self.assertEqual(c.get(reverse('ipd:admission_advise', args=[self.patient.id])).status_code, 200)
        self.assertEqual(c.get(reverse('ot:surgery_advise', args=[self.patient.id])).status_code, 200)
        # reception/OT queues
        self.assertEqual(c.get(reverse('ipd:admission_request_list')).status_code, 200)
        self.assertEqual(c.get(reverse('ot:surgery_request_list')).status_code, 200)
        # patient detail (has the new advise buttons)
        self.assertContains(c.get(reverse('patient_detail', args=[self.patient.id])), 'Advise Admission')

    def test_cancel_admission_request(self):
        ar = AdmissionRequest.objects.create(patient=self.patient, advised_by=self.doc_user,
                                             reason='x', hospital=self.h)
        c = Client(); c.force_login(self.admin)
        r = c.post(reverse('ipd:admission_request_cancel', args=[ar.id]))
        self.assertEqual(r.status_code, 302)
        ar.refresh_from_db()
        self.assertEqual(ar.status, 'Cancelled')


class NurseRoleTest(TestCase):
    """Ward Staff / Nurse: can do ward work (view admissions, log meds, rounds)
    but NOT admit, discharge or manage ward structure (billing/setup)."""
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=date.today() + timedelta(days=30))
        self.nurse = User.objects.create_user(email='n@n.com', password='pw', role='NURSE', hospital=self.h)
        self.doc_user = User.objects.create_user(email='d@d.com', password='pw', role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=self.doc_user, full_name='Dr D', opd_fee=Decimal('100'))
        self.patient = Patient.objects.create(full_name='P One', gender='M', hospital=self.h)
        self.ward = Ward.objects.create(name='Gen', ward_type='General Male',
                                        daily_rate=Decimal('1000'), hospital=self.h)
        self.bed = Bed.objects.create(bed_number='B1', ward=self.ward, status='Occupied', hospital=self.h)
        self.adm = Admission.objects.create(patient=self.patient, bed=self.bed,
                                            admission_reason='obs', attending_doctor=self.doctor,
                                            hospital=self.h)

    def test_nurse_can_do_ward_work(self):
        c = Client(); c.force_login(self.nurse)
        self.assertEqual(c.get(reverse('ipd:admission_list')).status_code, 200)
        self.assertEqual(c.get(reverse('ipd:admission_detail', args=[self.adm.pk])).status_code, 200)
        self.assertEqual(c.get(reverse('ipd:medication_log_add', args=[self.adm.pk])).status_code, 200)
        self.assertEqual(c.get(reverse('ipd:doctor_round_add', args=[self.adm.pk])).status_code, 200)
        self.assertEqual(c.get(reverse('ipd:ward_bed_list')).status_code, 200)

    def test_nurse_cannot_admit_or_discharge(self):
        c = Client(); c.force_login(self.nurse)
        self.assertEqual(c.get(reverse('ipd:admission_create')).status_code, 403)
        self.assertEqual(c.get(reverse('ipd:admission_discharge', args=[self.adm.pk])).status_code, 403)
        self.assertEqual(c.get(reverse('ipd:ward_create')).status_code, 403)
        self.assertEqual(c.get(reverse('ipd:admission_request_list')).status_code, 403)

    def test_medication_form_offers_pharmacy_medicines(self):
        """Ward staff search the pharmacy catalogue instead of typing from memory."""
        from decimal import Decimal as D
        from inventory.models import Medicine
        from saas.models import Hospital as H
        Medicine.objects.create(name='Panadol', brand='GSK', price=D('10'),
                                expiry_date=date.today() + timedelta(days=365),
                                hospital=self.h)
        other = H.objects.create(name='Other', slug='other',
                                 expiry_date=date.today() + timedelta(days=30))
        Medicine.objects.create(name='RivalMed', brand='X', price=D('10'),
                                expiry_date=date.today() + timedelta(days=365),
                                hospital=other)

        c = Client(); c.force_login(self.nurse)
        resp = c.get(reverse('ipd:medication_log_add', args=[self.adm.pk]))
        self.assertContains(resp, 'id="pharmacy-medicines"')
        self.assertContains(resp, 'Panadol (GSK)')
        self.assertNotContains(resp, 'RivalMed')          # other tenant's stock

    def test_nurse_can_order_lab_and_imaging_for_the_patient(self):
        """Ward staff raise the order; entering results stays with lab/radiology."""
        c = Client(); c.force_login(self.nurse)
        self.assertEqual(c.get(reverse('lab:order_create')).status_code, 200)
        self.assertEqual(c.get(reverse('imaging:study_create')).status_code, 200)
        # but NOT the rest of those modules
        self.assertEqual(c.get(reverse('lab:order_list')).status_code, 403)
        self.assertEqual(c.get(reverse('imaging:study_list')).status_code, 403)

    def test_admission_page_shows_allergies_and_orders(self):
        self.patient.allergies = 'Penicillin'
        self.patient.save()
        c = Client(); c.force_login(self.nurse)
        resp = c.get(reverse('ipd:admission_detail', args=[self.adm.pk]))
        self.assertContains(resp, 'Penicillin')
        self.assertContains(resp, 'Clinical Orders')
        self.assertContains(resp, 'Order Lab Test')

    def test_nurse_dashboard_lists_admitted(self):
        c = Client(); c.force_login(self.nurse)
        resp = c.get(reverse('dashboard'))          # home routes NURSE to their dashboard
        self.assertEqual(resp.status_code, 302)
        resp2 = c.get(reverse('user_mgmt:post_login_redirect'))
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'P One')


class WardMedicationStockAndBillingTest(TestCase):
    """Giving a drug on the ward must move stock and reach the discharge bill.

    Before this, the discharge invoice was bed charges only — every dose
    administered during a stay was given away free and never left inventory.
    """
    def setUp(self):
        from decimal import Decimal as D
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=30))
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        docuser = User.objects.create_user(email='d@d.com', password='pw',
                                           role='DOCTOR', hospital=self.h)
        self.doctor = Doctor.objects.create(user=docuser, full_name='Dr D',
                                            opd_fee=D('100'))
        self.patient = Patient.objects.create(full_name='Ward Patient', gender='M',
                                              mrn='WARD-1', hospital=self.h)
        self.ward = Ward.objects.create(name='Gen', ward_type='General Male',
                                        daily_rate=D('1000'), hospital=self.h)
        self.bed = Bed.objects.create(bed_number='B1', ward=self.ward,
                                      status='Occupied', hospital=self.h)
        self.adm = Admission.objects.create(patient=self.patient, bed=self.bed,
                                            admission_reason='obs',
                                            attending_doctor=self.doctor,
                                            hospital=self.h)
        from inventory.models import Medicine
        self.med = Medicine.objects.create(name='Panadol', brand='GSK', price=D('20'),
                                           expiry_date=date.today() + timedelta(days=365),
                                           hospital=self.h)
        self.med.add_stock(50, expiry_date=date.today() + timedelta(days=365),
                           cost_price=D('12'))

    def _give(self, client, qty=2, medicine_id=None):
        return client.post(reverse('ipd:medication_log_add', args=[self.adm.pk]), {
            'medicine': self.med.id if medicine_id is None else medicine_id,
            'medicine_name': 'Panadol (GSK)', 'dosage': '1 tablet',
            'quantity': qty, 'administered_at': '2026-07-22T10:00', 'notes': '',
        })

    def test_administering_reduces_pharmacy_stock(self):
        c = Client(); c.force_login(self.admin)
        before = self.med.sellable_quantity
        resp = self._give(c, qty=2)
        self.assertEqual(resp.status_code, 302)
        self.med.refresh_from_db()
        self.assertEqual(self.med.sellable_quantity, before - 2)

    def test_administering_freezes_the_price_of_the_day(self):
        from decimal import Decimal as D
        c = Client(); c.force_login(self.admin)
        self._give(c, qty=2)
        log = MedicationLog.objects.get()
        self.assertEqual(log.unit_price, D('20'))
        self.assertEqual(log.charge, D('40'))
        # a later catalogue price change must not rewrite what was billed
        self.med.price = D('99')
        self.med.save()
        log.refresh_from_db()
        self.assertEqual(log.charge, D('40'))

    def test_off_catalogue_drug_is_recorded_without_stock_or_charge(self):
        from decimal import Decimal as D
        c = Client(); c.force_login(self.admin)
        before = self.med.sellable_quantity
        resp = c.post(reverse('ipd:medication_log_add', args=[self.adm.pk]), {
            'medicine': '', 'medicine_name': 'Something from outside',
            'dosage': '1 amp', 'quantity': 1,
            'administered_at': '2026-07-22T10:00', 'notes': '',
        })
        self.assertEqual(resp.status_code, 302)
        log = MedicationLog.objects.get()
        self.assertIsNone(log.medicine)
        self.assertEqual(log.charge, D('0.00'))
        self.med.refresh_from_db()
        self.assertEqual(self.med.sellable_quantity, before)

    def test_cannot_give_more_than_is_in_stock(self):
        c = Client(); c.force_login(self.admin)
        resp = self._give(c, qty=999)
        self.assertEqual(resp.status_code, 200)          # form re-rendered
        self.assertEqual(MedicationLog.objects.count(), 0)
        self.med.refresh_from_db()
        self.assertEqual(self.med.sellable_quantity, 50)

    def test_discharge_bill_includes_medicines_not_just_the_bed(self):
        from decimal import Decimal as D
        from billing.models import Invoice
        c = Client(); c.force_login(self.admin)
        self._give(c, qty=2)                              # Rs 40 of medicine

        resp = c.post(reverse('ipd:admission_discharge', args=[self.adm.pk]),
                      {'discharge_notes': 'recovered'})
        self.assertEqual(resp.status_code, 302)

        invoice = Invoice.objects.get()
        descriptions = [i.description for i in invoice.items.all()]
        self.assertTrue(any('Bed Charges' in d for d in descriptions))
        self.assertTrue(any('Panadol' in d for d in descriptions),
                        f"medicines missing from the discharge bill: {descriptions}")
        # one day's bed (1000) + medicine (40)
        self.assertEqual(invoice.total, D('1040'))

    def test_allergy_warning_is_raised_after_administering(self):
        self.patient.allergies = 'Paracetamol, Panadol'
        self.patient.save()
        c = Client(); c.force_login(self.admin)
        resp = self._give(c, qty=1)
        messages = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any('ALLERGY' in m.upper() for m in messages),
                        f"no allergy warning raised: {messages}")


class DoctorSeesOnlyOwnInpatientsTest(TestCase):
    """A doctor follows their own admitted patients — the full ward chart for
    those, and nothing at all for a colleague's patient."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=30))
        self.mine_user = User.objects.create_user(email='mine@d.com', password='pw',
                                                  role='DOCTOR', hospital=self.h)
        self.mine = Doctor.objects.create(user=self.mine_user, full_name='Dr Mine',
                                          opd_fee=Decimal('100'))
        self.other_user = User.objects.create_user(email='other@d.com', password='pw',
                                                   role='DOCTOR', hospital=self.h)
        self.other = Doctor.objects.create(user=self.other_user, full_name='Dr Other',
                                           opd_fee=Decimal('100'))
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)

        self.ward = Ward.objects.create(name='Gen', ward_type='General Male',
                                        daily_rate=Decimal('1000'), hospital=self.h)
        bed1 = Bed.objects.create(bed_number='B1', ward=self.ward, status='Occupied', hospital=self.h)
        bed2 = Bed.objects.create(bed_number='B2', ward=self.ward, status='Occupied', hospital=self.h)

        self.my_patient = Patient.objects.create(full_name='My Patient', gender='M',
                                                 mrn='MRN-MINE', hospital=self.h)
        self.their_patient = Patient.objects.create(full_name='Their Patient', gender='F',
                                                    mrn='MRN-OTHER', hospital=self.h)
        self.my_adm = Admission.objects.create(patient=self.my_patient, bed=bed1,
                                               admission_reason='obs',
                                               attending_doctor=self.mine, hospital=self.h)
        self.their_adm = Admission.objects.create(patient=self.their_patient, bed=bed2,
                                                  admission_reason='obs',
                                                  attending_doctor=self.other, hospital=self.h)

    def test_list_shows_only_the_doctors_own_admissions(self):
        c = Client(); c.force_login(self.mine_user)
        body = c.get(reverse('ipd:admission_list')).content.decode()
        self.assertIn('My Patient', body)
        self.assertNotIn('Their Patient', body)

    def test_doctor_cannot_open_another_doctors_admission(self):
        c = Client(); c.force_login(self.mine_user)
        self.assertEqual(c.get(reverse('ipd:admission_detail', args=[self.my_adm.pk])).status_code, 200)
        self.assertEqual(c.get(reverse('ipd:admission_detail', args=[self.their_adm.pk])).status_code, 404)
        # and none of the write paths hanging off it either
        self.assertEqual(c.get(reverse('ipd:medication_log_add', args=[self.their_adm.pk])).status_code, 404)
        self.assertEqual(c.get(reverse('ipd:doctor_round_add', args=[self.their_adm.pk])).status_code, 404)
        self.assertEqual(c.get(reverse('ipd:admission_discharge', args=[self.their_adm.pk])).status_code, 404)

    def test_doctor_sees_the_medication_chart_of_their_own_patient(self):
        MedicationLog.objects.create(admission=self.my_adm, medicine_name='Panadol 500mg',
                                     dosage='1 tab', quantity=1,
                                     administered_by=self.admin, hospital=self.h)
        c = Client(); c.force_login(self.mine_user)
        body = c.get(reverse('ipd:admission_detail', args=[self.my_adm.pk])).content.decode()
        self.assertIn('Panadol 500mg', body)

    def test_doctor_keeps_the_patient_they_advised_even_under_another_attending(self):
        """Reception may allot a different attending doctor; the doctor who asked
        for the bed still follows that patient."""
        AdmissionRequest.objects.create(patient=self.their_patient, advised_by=self.mine_user,
                                        reason='needs a bed', status='Admitted',
                                        admission=self.their_adm, hospital=self.h)
        c = Client(); c.force_login(self.mine_user)
        self.assertEqual(c.get(reverse('ipd:admission_detail', args=[self.their_adm.pk])).status_code, 200)

    def test_admin_and_nurse_still_see_the_whole_ward(self):
        c = Client(); c.force_login(self.admin)
        body = c.get(reverse('ipd:admission_list')).content.decode()
        self.assertIn('My Patient', body)
        self.assertIn('Their Patient', body)

    def test_doctor_request_queue_shows_only_their_own_advices(self):
        AdmissionRequest.objects.create(patient=self.my_patient, advised_by=self.mine_user,
                                        reason='mine', hospital=self.h)
        AdmissionRequest.objects.create(patient=self.their_patient, advised_by=self.other_user,
                                        reason='theirs', hospital=self.h)
        c = Client(); c.force_login(self.mine_user)
        body = c.get(reverse('ipd:admission_request_list')).content.decode()
        self.assertIn('My Patient', body)
        self.assertNotIn('Their Patient', body)

    def test_doctor_has_the_inpatient_link_in_the_sidebar(self):
        c = Client(); c.force_login(self.mine_user)
        body = c.get(reverse('ipd:admission_list')).content.decode()
        sidebar = body.split('<aside', 1)[-1].split('</aside>', 1)[0]
        self.assertIn('My Inpatients', sidebar)
