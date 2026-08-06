"""Offline desktop-licence tests — crypto, state machine, and the lock middleware.

CI has no `private_key.json` (it is git-ignored), so these generate their own small
keypair and, for the state tests, patch `core.PUBLIC_KEY` to it. That is the whole
security property in one line: without the private key you cannot make a key the app
will accept, and the tests only pass because they mint their own keypair.
"""
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.test import TestCase, override_settings

from accounts.models import User
from user_mgmt import licensing as core
from licensing import keygen


class LicenseCryptoTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kp = keygen.generate(1024)          # small = fast; production key is 2048
        cls.pub = {"e": cls.kp["e"], "n": cls.kp["n"]}

    def _tok(self, days):
        return core.make_token("Clinic", date.today() + timedelta(days=days),
                               date.today(), self.kp)

    def test_valid_token_verifies(self):
        self.assertIsNotNone(core.read_token(self._tok(30), self.pub))

    def test_tampered_token_rejected(self):
        tok = self._tok(30)
        bad = tok[:-3] + ("xyz" if not tok.endswith("xyz") else "abc")
        self.assertIsNone(core.read_token(bad, self.pub))

    def test_token_from_another_key_rejected(self):
        other = keygen.generate(1024)
        self.assertIsNone(
            core.read_token(self._tok(30), {"e": other["e"], "n": other["n"]}))


class LicenseStateTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kp = keygen.generate(1024)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = core.PUBLIC_KEY
        core.PUBLIC_KEY = {"e": self.kp["e"], "n": self.kp["n"]}

    def tearDown(self):
        core.PUBLIC_KEY = self._orig

    def _state_file(self, **kw):
        Path(self.dir, "license.json").write_text(json.dumps(kw))

    def _key(self, days):
        return core.make_token("C", date.today() + timedelta(days=days),
                               date.today(), self.kp)

    def test_fresh_install_is_trial(self):
        st = core.license_state(self.dir)
        self.assertEqual(st["status"], "trial")
        self.assertTrue(st["ok"])

    def test_trial_lapses_to_locked(self):
        self._state_file(trial_start=(date.today()
                         - timedelta(days=core.TRIAL_DAYS + 1)).isoformat())
        st = core.license_state(self.dir)
        self.assertEqual(st["status"], "locked")
        self.assertFalse(st["ok"])

    def test_valid_key_unlocks(self):
        self.assertTrue(core.save_license(self.dir, self._key(40)))
        st = core.license_state(self.dir)
        self.assertEqual(st["status"], "licensed")
        self.assertTrue(st["ok"])
        self.assertEqual(st["days_left"], 40)

    def test_expired_key_locks(self):
        core.save_license(self.dir, self._key(-1))
        st = core.license_state(self.dir)
        self.assertEqual(st["status"], "expired")
        self.assertFalse(st["ok"])

    def test_invalid_key_is_not_stored(self):
        self.assertFalse(core.save_license(self.dir, "not-a-real-key"))
        self.assertEqual(core.license_state(self.dir)["status"], "trial")

    def test_clock_rollback_cannot_refresh_trial(self):
        core.license_state(self.dir, date(2026, 8, 20))     # last_seen -> 20 Aug
        st = core.license_state(self.dir, date(2026, 8, 1))  # wind the clock back
        self.assertEqual(st["days_left"], core.TRIAL_DAYS)   # trial did not reset

    def test_machine_locked_key_runs_on_its_own_machine(self):
        tok = core.make_token("C", date.today() + timedelta(days=30), date.today(),
                              self.kp, extra={"machine": core.machine_id()})
        core.save_license(self.dir, tok)
        st = core.license_state(self.dir)
        self.assertTrue(st["ok"])
        self.assertEqual(st["status"], "licensed")

    def test_machine_locked_key_refused_on_another_machine(self):
        tok = core.make_token("C", date.today() + timedelta(days=30), date.today(),
                              self.kp, extra={"machine": "some-other-computer-id"})
        core.save_license(self.dir, tok)
        st = core.license_state(self.dir)
        self.assertFalse(st["ok"])
        self.assertEqual(st["status"], "wrong_machine")

    def test_unlocked_key_runs_anywhere(self):
        tok = core.make_token("C", date.today() + timedelta(days=30), date.today(),
                              self.kp)                        # no machine field
        core.save_license(self.dir, tok)
        self.assertTrue(core.license_state(self.dir)["ok"])


class LicenseLockMiddlewareTest(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.admin = User.objects.create_user(
            email="admin@t.com", password="pw", role="ADMIN")
        self.client.force_login(self.admin)

    def _lapse_trial(self):
        Path(self.dir, "license.json").write_text(json.dumps(
            {"trial_start": (date.today()
                             - timedelta(days=core.TRIAL_DAYS + 1)).isoformat()}))

    @override_settings(DESKTOP_BUILD=True)
    def test_locked_build_blocks_the_app(self):
        with override_settings(DATA_DIR=Path(self.dir)):
            self._lapse_trial()
            resp = self.client.get("/")
            self.assertEqual(resp.status_code, 402)
            self.assertContains(resp, "Subscription needed", status_code=402)

    @override_settings(DESKTOP_BUILD=True)
    def test_license_page_stays_reachable_when_locked(self):
        with override_settings(DATA_DIR=Path(self.dir)):
            self._lapse_trial()
            self.assertEqual(self.client.get("/manage/license/").status_code, 200)

    def test_hosted_build_never_locks(self):
        # DESKTOP_BUILD is False in the test settings, so the middleware no-ops
        # even with a lapsed trial file present.
        self._lapse_trial()
        self.assertNotEqual(self.client.get("/").status_code, 402)
