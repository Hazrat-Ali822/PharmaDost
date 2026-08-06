"""Cloud backup (LAN install -> host) + in-app restore.

The upload endpoint authenticates by the install's signed licence, so — as with the
licensing tests — CI mints its own keypair and patches the app's PUBLIC_KEY to it.
"""
import io
import json
import shutil
import tempfile
import zipfile
from unittest import mock
from datetime import date, timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from user_mgmt import licensing as core
from licensing import keygen
from saas.models import DesktopBackup


def _backup_zip(extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("db.sqlite3", b"SQLite format 3\x00 (fake test db)")
        if extra:
            for name, body in extra.items():
                z.writestr(name, body)
    return buf.getvalue()


_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_MEDIA)
class BackupUploadTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.kp = keygen.generate(1024)
        self._orig = core.PUBLIC_KEY
        core.PUBLIC_KEY = {"e": self.kp["e"], "n": self.kp["n"]}
        self.token = core.make_token("Clinic A", date.today() + timedelta(days=30),
                                     date.today(), self.kp)

    def tearDown(self):
        core.PUBLIC_KEY = self._orig

    def _post(self, token):
        f = SimpleUploadedFile("b.zip", _backup_zip(), content_type="application/zip")
        return self.client.post("/saas/backup/upload/", {"token": token, "file": f})

    def test_valid_upload_is_stored(self):
        resp = self._post(self.token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(DesktopBackup.objects.filter(install_name="Clinic A").count(), 1)

    def test_invalid_licence_rejected(self):
        resp = self._post("garbage.key")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(DesktopBackup.objects.count(), 0)

    def test_missing_file_rejected(self):
        resp = self.client.post("/saas/backup/upload/", {"token": self.token})
        self.assertEqual(resp.status_code, 400)

    def test_only_latest_backup_is_kept(self):
        for _ in range(4):
            self._post(self.token)
        # One file per install: older snapshots are rotated off so the host disk
        # does not grow with every upload.
        self.assertEqual(DesktopBackup.objects.filter(install_name="Clinic A").count(), 1)


class BackupPortalTest(TestCase):
    def test_backup_list_is_superuser_only(self):
        staff = User.objects.create_user(email="s@t.com", password="pw", role="ADMIN")
        self.client.force_login(staff)
        resp = self.client.get("/saas/backups/")
        self.assertNotEqual(resp.status_code, 200)     # redirected to login

        owner = User.objects.create_superuser(email="o@t.com", password="pw")
        self.client.force_login(owner)
        self.assertEqual(self.client.get("/saas/backups/").status_code, 200)


class InstallSigningKeyTest(TestCase):
    def setUp(self):
        self.kp = keygen.generate(1024)
        self._orig = core.PUBLIC_KEY
        core.PUBLIC_KEY = {"e": self.kp["e"], "n": self.kp["n"]}
        self.owner = User.objects.create_superuser(email="o@t.com", password="pw")
        self.client.force_login(self.owner)
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        core.PUBLIC_KEY = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_matching_key_installs(self):
        f = SimpleUploadedFile("private_key.json", json.dumps(self.kp).encode(),
                               content_type="application/json")
        with override_settings(DATA_DIR=Path(self.dir)):
            resp = self.client.post("/saas/signing-key/", {"keyfile": f})
            self.assertEqual(resp.status_code, 302)          # redirected to generator
            self.assertTrue((Path(self.dir) / "private_key.json").exists())

    def test_mismatched_key_rejected(self):
        other = keygen.generate(1024)
        f = SimpleUploadedFile("private_key.json", json.dumps(other).encode())
        with override_settings(DATA_DIR=Path(self.dir)):
            self.client.post("/saas/signing-key/", {"keyfile": f})
            self.assertFalse((Path(self.dir) / "private_key.json").exists())


class HospitalLicenseGenTest(TestCase):
    def setUp(self):
        self.mock = mock
        self.kp = keygen.generate(1024)
        self._orig = core.PUBLIC_KEY
        core.PUBLIC_KEY = {"e": self.kp["e"], "n": self.kp["n"]}
        self.owner = User.objects.create_superuser(email="o@t.com", password="pw")
        self.client.force_login(self.owner)
        from saas.models import Hospital
        self.h = Hospital.objects.create(
            name="Shaheen Clinic", slug="shaheen",
            expiry_date=date.today() + timedelta(days=30))

    def tearDown(self):
        core.PUBLIC_KEY = self._orig

    def test_generated_key_is_bound_to_this_tenant(self):
        with self.mock.patch("saas.views._load_license_private_key", return_value=self.kp):
            resp = self.client.post(
                f"/saas/hospital/{self.h.pk}/desktop-license/", {"months": 3})
        self.assertEqual(resp.status_code, 200)
        token = resp.context["gen_token"]
        data = core.read_token(token)          # verifies signature
        self.assertEqual(data["clinic"], "Shaheen Clinic")
        self.assertEqual(data["slug"], "shaheen")

    def test_no_signing_key_shows_notice(self):
        with self.mock.patch("saas.views._load_license_private_key", return_value=None):
            resp = self.client.post(
                f"/saas/hospital/{self.h.pk}/desktop-license/", {"months": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["gen_token"])


@override_settings(DESKTOP_BUILD=True)
class RestoreStagingTest(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.admin = User.objects.create_user(email="a@t.com", password="pw", role="ADMIN")
        self.client.force_login(self.admin)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_valid_backup_is_staged_with_marker(self):
        with override_settings(DATA_DIR=Path(self.dir)):
            f = SimpleUploadedFile("backup.zip", _backup_zip(), content_type="application/zip")
            resp = self.client.post("/manage/restore/", {"backup": f})
            self.assertEqual(resp.status_code, 302)
            self.assertTrue((Path(self.dir) / "RESTORE_PENDING").exists())
            self.assertTrue((Path(self.dir) / "_restore_pending" / "db.sqlite3").exists())

    def test_not_a_backup_rejected(self):
        with override_settings(DATA_DIR=Path(self.dir)):
            f = SimpleUploadedFile("backup.zip", b"this is not a zip")
            self.client.post("/manage/restore/", {"backup": f})
            self.assertFalse((Path(self.dir) / "RESTORE_PENDING").exists())

    def test_zip_slip_rejected(self):
        evil = _backup_zip(extra={"../evil.txt": b"x"})
        with override_settings(DATA_DIR=Path(self.dir)):
            f = SimpleUploadedFile("backup.zip", evil)
            self.client.post("/manage/restore/", {"backup": f})
            self.assertFalse((Path(self.dir) / "RESTORE_PENDING").exists())
