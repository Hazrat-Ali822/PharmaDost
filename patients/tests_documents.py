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
            try:
                d.image.delete(save=False)
            except OSError:
                pass          # Windows holds a streamed file open; harmless here
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
            try:
                d.image.delete(save=False)
            except OSError:
                pass          # Windows holds a streamed file open; harmless here
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


class DocumentServingTest(TestCase):
    """The picture is served by a view, not from MEDIA_URL.

    Two separate reasons, and each on its own would be enough:

    * `django.conf.urls.static.static()` returns an empty list when DEBUG is
      False, so on the deployed host **nothing in Django serves /media/** — the
      thumbnails' visibility would depend on web-server config nobody checked.
    * A prescription photograph is a medical record. `/media/` is fetched with no
      session, so the file would be readable by anyone holding the URL.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-serve', expiry_date=_future())
        self.other = Hospital.objects.create(name='O', slug='o-serve', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.outsider = User.objects.create_user(email='x@o.com', password='pw',
                                                 role='ADMIN', hospital=self.other)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M', hospital=self.h)
        self.doc = PatientDocument.objects.create(
            patient=self.patient, hospital=self.h, uploaded_by=self.admin,
            image=_photo(), doc_date=date.today())

    def tearDown(self):
        for d in PatientDocument.all_objects.all():
            try:
                d.image.delete(save=False)
            except OSError:
                pass          # Windows holds a streamed file open; harmless here
        clear_current_hospital()

    def _url(self):
        return f'/patients/photo/{self.doc.pk}/file/'

    def test_a_signed_in_user_of_that_hospital_gets_the_image(self):
        c = Client(); c.login(email='a@a.com', password='pw')
        r = c.get(self._url())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')
        self.assertTrue(b''.join(r.streaming_content).startswith(b'\xff\xd8'))  # JPEG

    def test_a_signed_out_visitor_is_sent_to_the_login(self):
        r = Client().get(self._url())
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_another_hospital_cannot_read_it(self):
        c = Client(); c.login(email='x@o.com', password='pw')
        self.assertIn(c.get(self._url()).status_code, (403, 404))

    def test_a_missing_file_is_a_404_rather_than_a_500(self):
        """A database restored without its media, or a wiped upload folder."""
        self.doc.image.delete(save=False)
        c = Client(); c.login(email='a@a.com', password='pw')
        self.assertEqual(c.get(self._url()).status_code, 404)

    def test_the_response_is_not_cacheable_by_a_shared_proxy(self):
        c = Client(); c.login(email='a@a.com', password='pw')
        r = c.get(self._url())
        self.assertIn('private', r['Cache-Control'])
        r.close()          # Windows will not delete a file that is still open

    def test_the_record_page_links_the_view_not_the_media_url(self):
        c = Client(); c.login(email='a@a.com', password='pw')
        body = c.get(f'/patients/{self.patient.pk}/').content.decode()
        self.assertIn(self._url(), body)
        self.assertNotIn('/media/patient_docs/', body)


class DocumentThumbnailTest(TestCase):
    """The grid must not download full pictures to show postage stamps."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-thumb', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@a.com', password='pw',
                                              role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M', hospital=self.h)
        self.c = Client()
        self.c.login(email='a@a.com', password='pw')
        self.c.post(f'/patients/{self.patient.pk}/photo/add/',
                    {'image': _photo(), 'kind': 'RX',
                     'doc_date': date.today().strftime('%Y-%m-%d')})
        self.doc = PatientDocument.objects.get()

    def tearDown(self):
        for d in PatientDocument.all_objects.all():
            for f in (d.image, d.thumbnail):
                try:
                    f and f.delete(save=False)
                except OSError:
                    pass
        clear_current_hospital()

    def test_a_thumbnail_is_made_on_upload(self):
        from patients.images import THUMB_EDGE

        self.assertTrue(self.doc.thumbnail)
        self.assertLessEqual(max(self.doc.thumbnail.width, self.doc.thumbnail.height),
                             THUMB_EDGE)

    def test_the_thumbnail_is_much_smaller_than_the_picture(self):
        self.assertLess(self.doc.thumbnail.size, self.doc.image.size / 2)

    def test_the_grid_asks_for_the_thumbnail_and_links_the_full_picture(self):
        body = self.c.get(f'/patients/{self.patient.pk}/').content.decode()
        url = f'/patients/photo/{self.doc.pk}/file/'
        self.assertIn(f'src="{url}?thumb=1"', body)
        self.assertIn(f'href="{url}"', body)

    def test_a_row_with_no_thumbnail_falls_back_to_the_full_picture(self):
        """Rows predating thumbnails, and any whose thumbnail could not be made,
        must still show something."""
        self.doc.thumbnail.delete(save=False)
        self.doc.thumbnail = None
        self.doc.save(update_fields=['thumbnail'])
        r = self.c.get(f'/patients/photo/{self.doc.pk}/file/?thumb=1')
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_deleting_the_record_removes_both_files(self):
        image, thumb = self.doc.image.path, self.doc.thumbnail.path
        import os
        self.c.post(f'/patients/photo/{self.doc.pk}/delete/')
        self.assertFalse(os.path.exists(image))
        self.assertFalse(os.path.exists(thumb))


class DocumentRedirectTest(TestCase):
    """`next` comes from the request, so it is checked before it is followed."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-next', expiry_date=_future())
        set_current_hospital(self.h)
        User.objects.create_user(email='a@a.com', password='pw', role='ADMIN', hospital=self.h)
        self.patient = Patient.objects.create(full_name='Bilal', gender='M', hospital=self.h)
        self.c = Client()
        self.c.login(email='a@a.com', password='pw')

    def tearDown(self):
        for d in PatientDocument.all_objects.all():
            for f in (d.image, d.thumbnail):
                try:
                    f and f.delete(save=False)
                except OSError:
                    pass
        clear_current_hospital()

    def _post(self, next_url):
        return self.c.post(f'/patients/{self.patient.pk}/photo/add/',
                           {'image': _photo(), 'kind': 'RX',
                            'doc_date': date.today().strftime('%Y-%m-%d'),
                            'next': next_url})

    def test_it_returns_to_the_screen_the_user_came_from(self):
        r = self._post('/lab/order/7/')
        self.assertEqual(r['Location'], '/lab/order/7/')

    def test_an_offsite_next_is_ignored(self):
        """Otherwise this form is an open redirect: the user is on the real
        hospital domain, saves a photo, and lands on a copy of the login page."""
        r = self._post('https://evil.example/login/')
        self.assertEqual(r['Location'], f'/patients/{self.patient.pk}/')

    def test_a_scheme_relative_next_is_ignored_too(self):
        r = self._post('//evil.example/login/')
        self.assertEqual(r['Location'], f'/patients/{self.patient.pk}/')
