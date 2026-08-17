"""Dump the whole database to a JSON file that `loaddata` can actually read.

Use this instead of `dumpdata -o`, which is **not safe here**. Django writes the
`-o` file using Python's locale encoding, but `loaddata` always decodes it as
UTF-8. On Windows (cp1252) and on any host whose locale is POSIX/C, the two
disagree, and the failure is:

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 ...

0x97 is an em dash in cp1252, and this app writes em dashes into ordinary data —
"OPD Consultation — Dr. Sara Ahmed", "Delivery — Normal", "IPD Bed Charges: … —
2 Day(s)". So the dump of a real hospital's data is *guaranteed* to contain them,
and the import dies partway with a stack trace and a half-empty database. Found
by rehearsing the migration rather than by reasoning about it.

This writes the file with an explicit UTF-8 handle, so the encoding cannot depend
on which machine the command is run from. It also applies the exclusions the
migration needs, so they cannot be forgotten:

    python manage.py db_export ~/alldata.json

`contenttypes` and `auth.permission` are rebuilt by `migrate` on the target
database; loading the old rows on top collides on their unique constraints and
aborts the whole load. Sessions and admin log entries are disposable.
"""
import io
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

EXCLUDE = ['contenttypes', 'auth.permission', 'sessions.session',
           'admin.logentry']


class Command(BaseCommand):
    help = "Dump all data to a UTF-8 JSON file ready for loaddata on another database."

    def add_arguments(self, parser):
        parser.add_argument('output', help='File to write, e.g. ~/alldata.json')
        parser.add_argument('--indent', type=int, default=2)

    def handle(self, *args, **options):
        out = Path(options['output']).expanduser()

        # Held in memory: dumpdata has no incremental file API, and a clinic's
        # records are a few MB. If this ever grows past comfort, switch to
        # per-app files rather than reintroducing the encoding bug.
        buf = io.StringIO()
        try:
            call_command('dumpdata',
                         natural_foreign=True, natural_primary=True,
                         exclude=EXCLUDE, indent=options['indent'],
                         stdout=buf)
        except Exception as exc:
            raise CommandError(f'dump failed, nothing written: {exc}')

        payload = buf.getvalue()
        if not payload.strip():
            raise CommandError('the dump was empty — refusing to write a file '
                               'that would silently import nothing.')

        out.parent.mkdir(parents=True, exist_ok=True)
        # newline='' so Windows does not turn every \n into \r\n and inflate the
        # file; encoding is explicit so it does not follow the machine's locale.
        with open(out, 'w', encoding='utf-8', newline='') as fh:
            fh.write(payload)

        # Prove it round-trips before anyone relies on it. Reading it back the
        # way loaddata will is the only check that means anything.
        try:
            with open(out, 'rb') as fh:
                fh.read().decode('utf-8')
        except UnicodeDecodeError as exc:
            raise CommandError(f'the file just written is not valid UTF-8 '
                               f'({exc}) — do not use it.')

        size = out.stat().st_size
        self.stdout.write(self.style.SUCCESS(
            f'Wrote {size:,} bytes of UTF-8 JSON to {out}.\n'
            f'Load it on the new database with:  python manage.py loaddata {out}'))
