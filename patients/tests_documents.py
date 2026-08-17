"""Photographs of paper attached to a patient's record.

    python manage.py test patients.tests_documents --settings=pharma_mgmt.test_settings
"""
import io
from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from accounts.models import User
from patients.models import Patient, PatientDocument
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital


def _future():
    return date.today() + timedelta(days=365)


def _photo(name='rx.jpg', size=(2400, 3200), colour=(220, 220, 220)):
    """A JPEG the size a phone actually produces."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new('RGB', size, colour).save(buf, format='JPEG', quality=95)
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


class DocumentUploadTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-doc', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M',
                                              age_years=40, hospital=self.h)
        self.c = Client()
        self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        for d in PatientDocument.all_objects.all():
            d.image.delete(save=False)
        clear_current_hospital()

    def _post(self, **extra):
        data = {'image': _photo(), 'kind': 'RX',
                'doc_date': date.today().strftime('%Y-%m-%d')}
        data.update(extra)
        return self.c.post(f'/patients/{self.patient.pk}/photo/add/', data)

    def test_a_photo_is_saved_against_the_patient(self):
        self._post(title='Dr. Sara, BP medicines')
        doc = PatientDocument.objects.get()
        self.assertEqual(doc.patient, self.patient)
        self.assertEqual(doc.kind, 'RX')
        self.assertEqual(doc.uploaded_by, self.admin)
        self.assertEqual(doc.hospital, self.h)

    def test_the_stored_picture_is_shrunk(self):
        """A phone writes megabytes per shot; a prescription does not need them,
        and on the desktop build every one is copied into the backup zip."""
        from patients.images import MAX_EDGE

        self._post()
        doc = PatientDocument.objects.get()
        self.assertLessEqual(max(doc.image.width, doc.image.height), MAX_EDGE)

    def test_an_oversized_upload_is_refused_with_a_readable_message(self):
        from patients.images import MAX_UPLOAD_BYTES

        big = SimpleUploadedFile('huge.jpg', b'x' * (MAX_UPLOAD_BYTES + 1),
                                 content_type='image/jpeg')
        r = self.c.post(f'/patients/{self.patient.pk}/photo/add/',
                        {'image': big, 'kind': 'RX',
                         'doc_date': date.today().strftime('%Y-%m-%d')})
        self.assertEqual(r.status_code, 200)          # re-rendered, not saved
        self.assertEqual(PatientDocument.objects.count(), 0)

    def test_a_non_image_is_refused(self):
        bad = SimpleUploadedFile('notes.txt', b'just text', content_type='text/plain')
        self.c.post(f'/patients/{self.patient.pk}/photo/add/',
                    {'image': bad, 'kind': 'RX',
                     'doc_date': date.today().strftime('%Y-%m-%d')})
        self.assertEqual(PatientDocument.objects.count(), 0)

    def test_the_photo_shows_on_the_patient_record(self):
        self._post(title='Handwritten Rx')
        body = self.c.get(f'/patients/{self.patient.pk}/').content.decode()
        self.assertIn('Handwritten Rx', body)

    def test_a_photo_can_be_attached_to_a_particular_visit(self):
        from opd.models import Appointment, Doctor

        doctor = Doctor.objects.create(full_name='Sara Ahmed')
        appt = Appointment.objects.create(patient=self.patient, doctor=doctor,
                                          appointment_date=date.today())
        self._post(appointment_id=appt.pk)
        self.assertEqual(PatientDocument.objects.get().appointment, appt)

    def test_a_stray_appointment_id_is_ignored_not_trusted(self):
        """It arrives in a POST body; it must be checked against this patient."""
        from opd.models import Appointment, Doctor

        other = Patient.objects.create(full_name='Someone Else', gender='F', hospital=self.h)
        doctor = Doctor.objects.create(full_name='Sara Ahmed')
        theirs = Appointment.objects.create(patient=other, doctor=doctor,
                                            appointment_date=date.today())
        self._post(appointment_id=theirs.pk)
        self.assertIsNone(PatientDocument.objects.get().appointment)


class DocumentAccessTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-acc', expiry_date=_future())
        self.other = Hospital.objects.create(name='O', slug='o-acc', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.pharmacist = User.objects.create_user(email='p@a.com', password='pw',
                                                   role='PHARMACIST', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M', hospital=self.h)
        self.their_patient = Patient.objects.create(full_name='Theirs', gender='M',
                                                    hospital=self.other)

    def tearDown(self):
        for d in PatientDocument.all_objects.all():
            d.image.delete(save=False)
        clear_current_hospital()

    def test_the_pharmacist_can_attach_a_photo_although_they_lack_patients(self):
        """They are the one holding the paper when the patient reaches the
        counter — hence the view is gated on `patients` OR `pos`."""
        self.assertFalse(self.pharmacist.has_feature('patients'))
        c = Client(); c.login(email='p@a.com', password='pw')
        r = c.get(f'/patients/{self.patient.pk}/photo/add/')
        self.assertEqual(r.status_code, 200)

    def test_another_hospitals_patient_is_not_reachable(self):
        c = Client(); c.login(email='a@a.com', password='pw')
        r = c.get(f'/patients/{self.their_patient.pk}/photo/add/')
        self.assertIn(r.status_code, (403, 404))

    def test_only_an_admin_or_the_uploader_can_remove_a_photo(self):
        c = Client(); c.login(email='p@a.com', password='pw')
        c.post(f'/patients/{self.patient.pk}/photo/add/',
               {'image': _photo(), 'kind': 'RX',
                'doc_date': date.today().strftime('%Y-%m-%d')})
        doc = PatientDocument.objects.get()

        nurse = User.objects.create_user(email='n@a.com', password='pw',
                                         role='NURSE', hospital=self.h)
        c2 = Client(); c2.login(email='n@a.com', password='pw')
        c2.post(f'/patients/photo/{doc.pk}/delete/')
        self.assertTrue(PatientDocument.objects.filter(pk=doc.pk).exists())

        admin_c = Client(); admin_c.login(email='a@a.com', password='pw')
        admin_c.post(f'/patients/photo/{doc.pk}/delete/')
        self.assertFalse(PatientDocument.objects.filter(pk=doc.pk).exists())


class ImageCompressionTest(TestCase):

    def test_a_portrait_photo_keeps_its_orientation(self):
        """Phones store rotation in EXIF instead of rotating the pixels, and a
        prescription displayed on its side is as useless as not having it."""
        from PIL import Image

        from patients.images import compress

        buf = io.BytesIO()
        img = Image.new('RGB', (1200, 800), (200, 200, 200))
        exif = img.getexif()
        exif[274] = 6                       # Orientation: rotate 90° CW
        img.save(buf, format='JPEG', exif=exif)
        upload = SimpleUploadedFile('p.jpg', buf.getvalue(), content_type='image/jpeg')

        out = Image.open(compress(upload))
        self.assertGreater(out.height, out.width, 'EXIF rotation was not applied')

    def test_an_unreadable_file_is_stored_rather_than_lost(self):
        """Losing the record to save disk space would be the wrong trade."""
        from patients.images import compress

        broken = SimpleUploadedFile('x.jpg', b'not really a jpeg',
                                    content_type='image/jpeg')
        self.assertIs(compress(broken), broken)

    def test_a_small_image_is_not_scaled_up(self):
        """Re-encoding may still shrink the bytes, but the pixels stay as they
        were — an upscaled photograph is no more readable, only heavier."""
        from PIL import Image

        from patients.images import compress

        out = Image.open(compress(_photo(size=(300, 400))))
        self.assertEqual(out.size, (300, 400))
