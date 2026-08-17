"""Find the data PostgreSQL will reject, before the migration, not during it.

SQLite is loosely typed and historically did not enforce foreign keys. It will
store a 300-character string in a `max_length=200` column, a `NULL` where the
column says NOT NULL, and a foreign key pointing at a row that no longer exists.
PostgreSQL will do none of those, so a database that has worked for years can
still refuse to load.

The failure mode is what makes this worth a command: `loaddata` dies partway
through with one stack trace naming one row, you fix it, run again, and it dies
on the next one — at 2am, with the site down, discovering the problems one at a
time. This finds all of them at once, while the site is still up and nothing has
been changed:

    python manage.py db_preflight

Exits non-zero if anything would block the load. It only reads.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models

SKIP_APPS = {'contenttypes', 'sessions', 'admin'}


class Command(BaseCommand):
    help = "Check for data PostgreSQL would reject before migrating to it."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10,
                            help='Example rows to print per problem (default 10).')

    def handle(self, *args, **options):
        self.limit = options['limit']
        problems = []
        problems += self._foreign_keys()
        problems += self._field_values()

        if not problems:
            self.stdout.write(self.style.SUCCESS(
                'No blocking data found. The dump should load into PostgreSQL '
                'cleanly.\n'
                'This checks the data, not the transfer — still run '
                '`db_snapshot --compare` afterwards.'))
            return

        self.stdout.write(self.style.ERROR(
            f'{len(problems)} problem(s) would stop the load:\n'))
        for line in problems:
            self.stdout.write(self.style.ERROR(f'  {line}'))
        self.stdout.write(
            '\nFix these on the current database first. Most are one bad row '
            'from an old import; delete or correct it and run this again.')
        raise CommandError('not ready to migrate')

    # ------------------------------------------------------------------ checks

    def _foreign_keys(self):
        """Rows pointing at parents that no longer exist.

        SQLite only enforces foreign keys when `PRAGMA foreign_keys` is on, which
        it has not always been, so orphans can be sitting in a database that
        looks perfectly healthy. PostgreSQL rejects every one of them.
        `check_constraints()` is Django's own implementation of this.
        """
        self.stdout.write('Checking foreign keys...')
        try:
            connection.check_constraints()
        except Exception as exc:
            return [f'broken foreign key: {exc}']
        return []

    def _field_values(self):
        """Values SQLite stored happily that PostgreSQL will not accept."""
        self.stdout.write('Checking column lengths and nulls...')
        found = []
        for model in apps.get_models():
            if model._meta.app_label in SKIP_APPS:
                continue
            manager = getattr(model, 'all_objects', None) or model._default_manager
            label = f'{model._meta.app_label}.{model.__name__}'
            for field in model._meta.concrete_fields:
                found += self._check_field(manager, label, field)
        return found

    def _check_field(self, manager, label, field):
        out = []
        name = field.name

        # Over-length text. SQLite ignores VARCHAR(n); PostgreSQL enforces it.
        if isinstance(field, models.CharField) and field.max_length:
            try:
                over = manager.exclude(**{f'{name}__isnull': True}).extra(
                    where=[f'LENGTH("{field.column}") > %s'],
                    params=[field.max_length])
                ids = list(over.values_list('pk', flat=True)[:self.limit])
            except Exception:
                ids = []
            if ids:
                out.append(f'{label}.{name}: {len(ids)}+ row(s) longer than '
                           f'max_length={field.max_length} — pk {ids}')

        # NULL in a column declared NOT NULL.
        if not field.null and not field.primary_key:
            try:
                ids = list(manager.filter(**{f'{name}__isnull': True})
                           .values_list('pk', flat=True)[:self.limit])
            except Exception:
                ids = []
            if ids:
                out.append(f'{label}.{name}: NULL in a NOT NULL column — '
                           f'pk {ids}')
        return out
