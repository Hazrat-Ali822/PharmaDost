"""The migration verifier must not be able to lie.

`db_snapshot` exists to prove no rows were lost moving SQLite -> PostgreSQL, so
the one thing it may never do is report success (or a clean "0 rows") when it
could not actually read the data.

    python manage.py test saas.tests_snapshot --settings=pharma_mgmt.test_settings
"""
import io
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from accounts.models import User
from patients.models import Patient
from saas.models import Hospital
from saas.utils import clear_current_hospital
from user_mgmt.models import UserProfile


def _future():
    return date.today() + timedelta(days=365)


class DbSnapshotTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-snap',
                                         expiry_date=_future())
        Patient.objects.create(full_name='One', gender='M', hospital=self.h)
        Patient.objects.create(full_name='Two', gender='F', hospital=self.h)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        clear_current_hospital()

    def _save(self, name='before.json'):
        path = self.tmp / name
        call_command('db_snapshot', save=str(path), verbosity=0)
        return path

    def test_it_counts_every_tenant_not_just_the_bound_one(self):
        """A command binds no hospital, so counting through `objects` would
        report only the hospital-less rows and call the rest missing."""
        data = json.loads(self._save().read_text(encoding='utf-8'))
        self.assertEqual(data['patients.Patient'], 2)

    def test_an_unchanged_database_compares_clean(self):
        path = self._save()
        call_command('db_snapshot', compare=str(path), verbosity=0)   # no raise

    def test_a_missing_row_is_reported(self):
        path = self._save()
        Patient.objects.all().delete()
        with self.assertRaises(CommandError):
            call_command('db_snapshot', compare=str(path), verbosity=0)

    def test_an_extra_row_is_reported_too(self):
        """A load run twice duplicates rather than loses, and that is just as
        wrong."""
        path = self._save()
        Patient.objects.create(full_name='Three', gender='M', hospital=self.h)
        with self.assertRaises(CommandError):
            call_command('db_snapshot', compare=str(path), verbosity=0)

    def test_an_unreachable_database_raises_rather_than_reporting_zero(self):
        """The first version swallowed per-model errors and then printed
        '0 rows' — indistinguishable from an empty database, which is exactly
        the silent failure this command is meant to catch."""
        with mock.patch('django.db.connection.ensure_connection',
                        side_effect=OSError('connection refused')):
            with self.assertRaises(CommandError) as caught:
                call_command('db_snapshot', verbosity=0)
        self.assertIn('cannot reach the database', str(caught.exception))

    def test_a_model_that_cannot_be_counted_aborts_the_snapshot(self):
        """Patching the manager *class* stands in for "the table is not there
        yet" — a half-finished migrate, which is exactly when someone would run
        this and be reassured by a partial answer."""
        path = self.tmp / 'x.json'
        manager_class = type(Patient._default_manager)
        with mock.patch.object(manager_class, 'count',
                               side_effect=RuntimeError('no such table')):
            with self.assertRaises(CommandError) as caught:
                call_command('db_snapshot', save=str(path), verbosity=0)
        self.assertIn('would be a lie', str(caught.exception))
        self.assertFalse(path.exists(), 'a broken snapshot must not be written')


class DbExportEncodingTest(TestCase):
    """`dumpdata -o` writes the file in the machine's locale encoding, but
    `loaddata` always decodes UTF-8. This app writes em dashes into ordinary
    data — "OPD Consultation — Dr. Sara Ahmed" — so on Windows (cp1252) or any
    POSIX/C-locale host the dump of a real hospital dies on import with

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97

    half way through, leaving a partly-filled database. Found by rehearsing the
    migration; `db_export` writes an explicit UTF-8 handle so the encoding
    cannot depend on which machine ran the command.
    """

    def setUp(self):
        self.h = Hospital.objects.create(name='H — Dash', slug='h-dash',
                                         expiry_date=_future())
        Patient.objects.create(full_name='Ali — Khan', gender='M',
                               hospital=self.h)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        clear_current_hospital()

    def test_the_dump_is_utf8_and_keeps_the_em_dashes(self):
        path = self.tmp / 'data.json'
        call_command('db_export', str(path), verbosity=0)
        # Decoded the way loaddata will decode it — that is the whole test.
        text = path.read_bytes().decode('utf-8')
        self.assertIn('Ali — Khan', text)
        self.assertIn('H — Dash', text)

    def test_it_excludes_what_migrate_rebuilds(self):
        """Loading old contenttypes/permissions on top of freshly migrated ones
        collides on their unique constraints and aborts the entire load."""
        path = self.tmp / 'data.json'
        call_command('db_export', str(path), verbosity=0)
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('"contenttypes.contenttype"', text)
        self.assertNotIn('"auth.permission"', text)


class DbPreflightTest(TestCase):
    """SQLite stores things PostgreSQL will refuse. Find them before the move,
    not one stack trace at a time at 2am with the site down."""

    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h-pre',
                                         expiry_date=_future())

    def tearDown(self):
        clear_current_hospital()

    def test_clean_data_passes(self):
        Patient.objects.create(full_name='Fine', gender='M', hospital=self.h)
        call_command('db_preflight', verbosity=0)          # no raise

    def test_an_over_length_value_is_caught(self):
        """SQLite ignores VARCHAR(n) entirely; PostgreSQL rejects the row."""
        field = Patient._meta.get_field('full_name')
        Patient.objects.create(full_name='x' * (field.max_length + 50),
                               gender='M', hospital=self.h)
        with self.assertRaises(CommandError):
            call_command('db_preflight', verbosity=0)


class FixtureRoundTripTest(TestCase):
    """A dump of this database must load back into an empty one.

    That is the whole SQLite -> PostgreSQL move, and on the real data it stopped
    partway with

        Could not load user_mgmt.UserProfile(pk=5): duplicate key value
        violates unique constraint "user_mgmt_userprofile_user_id_key"

    because a `post_save` receiver created a profile for every user the fixture
    inserted, taking the pks the fixture's own profiles were meant to land on.
    `db_preflight` cannot see this — the source data is perfectly valid; it is
    the *load* that invents rows.

    The check is deliberately the round trip rather than that one receiver: any
    other signal that starts writing during a fixture load fails here too.
    """

    def _dump(self, *labels):
        buf = io.StringIO()
        call_command('dumpdata', *labels, natural_foreign=True,
                     natural_primary=True, stdout=buf)
        return buf.getvalue()

    def _load(self, payload):
        path = Path(tempfile.mkdtemp()) / 'data.json'
        path.write_text(payload, encoding='utf-8')
        call_command('loaddata', str(path), verbosity=0)

    def test_a_dump_of_users_and_profiles_loads_back(self):
        for i in range(3):
            User.objects.create_user(email=f'u{i}@x.com', password='x')
        self.assertEqual(UserProfile.objects.count(), 3)

        payload = self._dump('accounts.User', 'user_mgmt.UserProfile')

        User.objects.all().delete()          # cascades the profiles away
        self.assertEqual(UserProfile.objects.count(), 0)

        self._load(payload)

        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(UserProfile.objects.count(), 3,
                         'the load created profiles of its own on top of the '
                         "fixture's — the duplicate-key failure")

    def test_every_user_still_gets_a_profile_normally(self):
        """The guard is for fixture loads only; ordinary sign-up is
        unchanged."""
        u = User.objects.create_user(email='normal@x.com', password='x')
        self.assertTrue(UserProfile.objects.filter(user=u).exists())
