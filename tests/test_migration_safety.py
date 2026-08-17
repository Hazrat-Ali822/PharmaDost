"""No migration of ours may change the schema after it has written data.

Each migration runs inside one transaction. Once a `RunPython`/`RunSQL` step has
written rows, PostgreSQL has deferred foreign-key trigger events queued, and it
then refuses DDL touching the tables those events reference:

    django.db.utils.OperationalError: cannot ALTER TABLE "hr_shift"
    because it has pending trigger events

`ipd/0010` did exactly that — AddField, a RunPython that created rows, then a
RenameField. **The whole suite passed**, because SQLite has no deferred
constraint triggers and cannot produce that error, so it surfaced only on the
real database, mid-deploy. That is exactly the class of bug that has to be
caught structurally: the test database will never reproduce it.

Hence the rule, checked by reading the migration files rather than running them:
put the data step in a migration of its own, with the schema work before it and
after it in migrations of their own. Schema *before* data inside one migration
is fine and common (add a column, then backfill it) — it is DDL **after** DML
that fails.

    python manage.py test tests.test_migration_safety --settings=pharma_mgmt.test_settings
"""
from pathlib import Path

from django.conf import settings
from django.db import migrations
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR).resolve()

# Steps that write rows.
DATA_OPS = (migrations.RunPython, migrations.RunSQL)

# Steps that touch Django's model state only and emit no SQL, so they are
# harmless after a data step. Everything else counts as schema work —
# AlterUniqueTogether and AlterIndexTogether included, since those do emit DDL.
STATE_ONLY = (
    migrations.AlterModelOptions,
    migrations.AlterModelManagers,
)

# Unavoidable exceptions, written down with a reason rather than weakening the
# rule. Empty today.
EXEMPT: set[str] = set()


def _is_ours(migration):
    """Skip Django's own and any third-party migrations — not ours to fix.

    `contenttypes.0002` legitimately trips this rule and lives in site-packages.
    """
    module = __import__(migration.__module__, fromlist=['__file__'])
    path = getattr(module, '__file__', None)
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    # `.venv` lives inside the project here, so "under BASE_DIR" alone would
    # claim every installed package's migrations as ours.
    if 'site-packages' in resolved.parts:
        return False
    return BASE_DIR in resolved.parents


class MigrationOrderTest(SimpleTestCase):

    def test_no_migration_changes_the_schema_after_writing_data(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        offenders = []

        for (app_label, name), migration in loader.disk_migrations.items():
            if f'{app_label}.{name}' in EXEMPT or not _is_ours(migration):
                continue

            seen_data = False
            for op in migration.operations:
                if isinstance(op, DATA_OPS):
                    seen_data = True
                elif seen_data and not isinstance(op, STATE_ONLY):
                    offenders.append(
                        f'{app_label}.{name}: {type(op).__name__} runs after a data '
                        f'step — move the data step into a migration of its own')
                    break

        self.assertEqual(offenders, [], '\n' + '\n'.join(offenders))

    def test_the_check_actually_looks_at_our_migrations(self):
        """A filter that quietly excluded everything would make the test above
        pass for the wrong reason."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        ours = [f'{a}.{n}' for (a, n), m in loader.disk_migrations.items() if _is_ours(m)]
        self.assertGreater(len(ours), 50, 'the "is it ours" filter is excluding too much')
        self.assertIn('ipd.0011_shift_fk_data', ours)
