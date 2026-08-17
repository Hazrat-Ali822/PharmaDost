"""The migration verifier must not be able to lie.

`db_snapshot` exists to prove no rows were lost moving SQLite -> PostgreSQL, so
the one thing it may never do is report success (or a clean "0 rows") when it
could not actually read the data.

    python manage.py test saas.tests_snapshot --settings=pharma_mgmt.test_settings
"""
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
