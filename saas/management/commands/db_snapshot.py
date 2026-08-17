"""Count every row in the database, and compare two counts.

Moving from SQLite to PostgreSQL is a `dumpdata` / `loaddata` round trip, and the
way that goes wrong is **quietly**: a model that failed to load leaves the site
working, the screens you happen to open look fine, and the missing rows are found
weeks later when somebody asks for a patient who is not there.

So take a count before, take a count after, and diff them:

    # on the old (SQLite) database
    python manage.py db_snapshot --save before.json

    # after loading into PostgreSQL
    python manage.py db_snapshot --compare before.json

`--compare` exits **non-zero** when anything differs, so it can gate a script.

Counts go through each model's **unfiltered** manager where there is one
(`all_objects`), because a command binds no tenant and `TenantManager` would
otherwise report only the hospital-less rows and call the rest missing.
"""
import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

# Django rebuilds these itself; they legitimately differ across databases and a
# diff on them is noise, not data loss.
SKIP = {'contenttypes.ContentType', 'auth.Permission', 'sessions.Session',
        'admin.LogEntry'}


def _counts():
    """{'app.Model': n}. Raises if the database cannot be read at all.

    Failing loudly here is the whole point. The first version caught every
    exception per model, so a database that was simply unreachable reported
    every model as an error and then printed "0 rows" — indistinguishable from
    an empty database, which is precisely the quiet failure this command exists
    to catch. A tool that can lie about the thing it verifies is worse than no
    tool.
    """
    from django.db import connection

    try:
        connection.ensure_connection()
    except Exception as exc:
        raise CommandError(f'cannot reach the database: {exc}')

    out, broken = {}, []
    for model in apps.get_models():
        label = f'{model._meta.app_label}.{model.__name__}'
        if label in SKIP:
            continue
        manager = getattr(model, 'all_objects', None) or model._default_manager
        try:
            out[label] = manager.count()
        except Exception as exc:
            broken.append(f'  {label}: {exc}')
    if broken:
        raise CommandError(
            'These models could not be counted, so the snapshot would be a '
            'lie:\n' + '\n'.join(broken))
    return dict(sorted(out.items()))


class Command(BaseCommand):
    help = "Row counts for every model; save them, or compare against a saved file."

    def add_arguments(self, parser):
        parser.add_argument('--save', metavar='FILE',
                            help='Write the counts to this JSON file.')
        parser.add_argument('--compare', metavar='FILE',
                            help='Compare current counts against this file and '
                                 'exit non-zero if anything differs.')

    def handle(self, *args, **options):
        counts = _counts()

        if options['save']:
            with open(options['save'], 'w', encoding='utf-8') as fh:
                json.dump(counts, fh, indent=2)
            total = sum(counts.values())
            self.stdout.write(self.style.SUCCESS(
                f"Saved {len(counts)} model counts ({total} rows) to "
                f"{options['save']}."))
            return

        if options['compare']:
            try:
                with open(options['compare'], encoding='utf-8') as fh:
                    before = json.load(fh)
            except OSError as exc:
                raise CommandError(f'cannot read {options["compare"]}: {exc}')

            problems = []
            for label in sorted(set(before) | set(counts)):
                was, now = before.get(label, '(absent)'), counts.get(label, '(absent)')
                if was != now:
                    problems.append(f'  {label}: was {was}, now {now}')
            if problems:
                self.stdout.write(self.style.ERROR(
                    'These do NOT match — do not go live until they do:'))
                for line in problems:
                    self.stdout.write(self.style.ERROR(line))
                raise CommandError(f'{len(problems)} model(s) differ.')
            self.stdout.write(self.style.SUCCESS(
                f'All {len(counts)} models match. Every row made it across.'))
            return

        for label, n in counts.items():
            if n:
                self.stdout.write(f'{label}: {n}')
        total = sum(counts.values())
        self.stdout.write(self.style.SUCCESS(f'{total} rows in {len(counts)} models.'))
