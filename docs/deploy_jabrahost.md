# Deploying Sehatyar to cPanel + Passenger (LiteSpeed / CloudLinux)

This is the shared-hosting path — any cPanel host with **Setup Python App**
(the CloudLinux "Python Selector", used on LiteSpeed hosts too). It runs Django
through Passenger and uses **SQLite** — a single file, no database server to
configure, and it sidesteps the one MySQL limitation this app has (conditional
unique indexes for MRN, which MySQL silently drops).

> Before buying the plan, confirm cPanel has **Setup Python App**, **Python 3.10+**,
> and **SSH / Terminal access**. A PHP-only plan cannot run Django.

**Hosting alongside other sites is fine.** Each "Setup Python App" is its own
isolated virtualenv and domain mapping, so Sehatyar on `sehatyar.online` and an
unrelated site on another domain (e.g. `khabirconsultant.ae`) coexist without
touching each other. Put the app in its own folder, **not** in `public_html`.

## 1. Upload the code

Either `git clone` in the Terminal, or upload a zip via File Manager and extract
it. Put it in a folder like `/home/<cpaneluser>/sehatyar` (e.g.
`/home/sehatyar/sehatyar`) — **outside** `public_html`; Passenger maps the
domain to it for you.

## 2. Create the Python app

cPanel → **Setup Python App** → Create:
- Python version: **3.10+**
- Application root: `sehatyar` (the folder from step 1)
- Application URL: your domain
- Application startup file: `passenger_wsgi.py` (already in the repo)

Create it. cPanel makes a virtualenv and shows the command to activate it
(`source /home/<cpaneluser>/virtualenv/sehatyar/3.10/bin/activate`).

## 3. Install dependencies

In the SSH terminal, activate the virtualenv (command from step 2), then:

```bash
cd ~/sehatyar
pip install -r requirements.txt
```

SQLite needs no driver. (Only if you later switch to MySQL: `pip install mysqlclient`.)

## 4. Create `.env` in the project root

```bash
cd ~/sehatyar
python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64))" >> .env
echo "DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com" >> .env
echo "DJANGO_CSRF_TRUSTED=https://yourdomain.com,https://www.yourdomain.com" >> .env
```

- **`DJANGO_SECRET_KEY`** is required — the app refuses to start on a server
  without it (it signs login cookies). Run that line **once**.
- **`DJANGO_ALLOWED_HOSTS`** must list your real domain, or Django returns
  "Bad Request (400)".
- **`DJANGO_CSRF_TRUSTED`** lets forms POST over HTTPS.
- Do **not** set `DATABASE_URL` — leaving it out keeps the app on SQLite.
- Leave `DJANGO_DEBUG` unset — under `/home/` it defaults to off, which is right.

If the domain has no SSL yet, add `DJANGO_SSL=false` for now so login cookies work
over plain http; remove it once you enable free Let's Encrypt SSL in cPanel.

## 5. Migrate and collect static

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

**The live site runs on PostgreSQL** (`sehatyar_prod`), configured by the
`DATABASE_URL` line in `.env`. It began on SQLite and was moved across —
`docs/migrate_to_postgres.md` has that procedure. `~/sehatyar/db.sqlite3` is
still on disk but nothing reads it; copying it is not a backup of anything.

Back up with `pg_dump`, and back up `MEDIA_ROOT` separately — uploaded logos and
patient document photos live there, and no database dump covers them:

```bash
cd ~/sehatyar && mkdir -p backups
source ~/virtualenv/sehatyar/3.10/bin/activate

PGPASSWORD='<db password>' pg_dump -h localhost -U sehatyar_dbuser   -d sehatyar_prod -F c -f "backups/pg-$(date +%F-%H%M).dump"

MEDIA=$(python manage.py shell -c "from django.conf import settings; print(settings.MEDIA_ROOT)")
tar czf "backups/media-$(date +%F).tar.gz" -C "$(dirname "$MEDIA")" "$(basename "$MEDIA")"

ls -lh backups/ | tail -3
```

Two things to get right here.

**Use the plain password**, not the percent-encoded form from `DATABASE_URL`:
`%28` and `%23` are URL escaping for `(` and `#`, and `pg_dump` takes the real
characters.

**Ask Django where the media is; do not type the path.** `MEDIA_ROOT` is
`DATA_DIR / "media"`, and `DATA_DIR` is `PHARMADOST_DATA_DIR` or `BASE_DIR` — so
on this host it is `~/sehatyar/media`, but on the desktop build it is a per-user
folder somewhere else entirely. Guessing `data/media` produced a **45-byte
tar.gz**: `tar` warned on stderr, exited non-zero, and left behind a file that
sits in the backups folder looking exactly like a backup. Check the size of what
you just wrote — that is the whole difference between having a backup and
believing you have one.

## 6. Create the platform owner (SaaS multi-tenant)

This is a **multi-tenant SaaS** install: one platform owner (superuser) who then
creates each hospital tenant from the owner portal. Create that superuser:

```bash
python manage.py createsuperuser
```

Log in with it → a superuser with **no hospital** lands on the **SaaS Owner Portal**
(`/saas/`). From there, **Create Hospital** provisions each tenant (name, slug,
monthly price, expiry, modules) *and* its first admin account in one step. Each
tenant then logs in at `https://yourdomain.com/<slug>/`.

Because a user now exists, the single-site first-run wizard is skipped — that wizard
is only for a standalone one-hospital install, not this SaaS deployment.
(`python manage.py seed_demo` still loads a demo tenant — passwords `pharma123` — if
you want sample data to look at first.)

## 7. Restart

cPanel → Setup Python App → **Restart**. Any code change needs a restart to take
effect (like the Reload button on PythonAnywhere).

## Updating later

```bash
cd ~/sehatyar && git pull
source ~/virtualenv/sehatyar/3.10/bin/activate
pip install -r requirements.txt          # only if requirements changed
python manage.py migrate                 # only if there are new migrations
python manage.py collectstatic --noinput # only if static changed
# then: Setup Python App → Restart
```

## Cron (daily alerts)

cPanel → **Cron Jobs**, one daily line (adjust the path):

```
cd ~/sehatyar && ~/virtualenv/sehatyar/3.10/bin/python manage.py expiry_alert && ~/virtualenv/sehatyar/3.10/bin/python manage.py low_stock_alert && ~/virtualenv/sehatyar/3.10/bin/python manage.py send_reminders && PGPASSWORD='<db password>' pg_dump -h localhost -U sehatyar_dbuser -d sehatyar_prod -F c -f ~/sehatyar/backups/pg-$(date +\%F).dump && tar czf ~/sehatyar/backups/media-$(date +\%F).tar.gz -C ~/sehatyar media
```

Three notes on that line:

- **`send_reminders` belongs here** — it is the patient-facing half (tomorrow's
  appointments, "your lab report is ready", vaccination due). It is safe to run
  twice: every message carries a `dedupe_key` and `already_sent` refuses a repeat.
- **`%F` is escaped as `\%F`.** cron treats an unescaped `%` as end-of-command
  and turns the rest into stdin, so the dump would be written to a file literally
  named `pg-` and the reminders would look like they ran but the backup would not.
- The free tier allows only **one** scheduled task, which is why everything is
  chained into this single daily line.

## If you insist on MySQL

MySQL works but two things must be handled first, so tell the maintainer before
switching:
1. `pip install mysqlclient`, and set `DATABASE_URL=mysql://user:pass@localhost/dbname` in `.env`.
2. The MRN uniqueness uses **conditional unique indexes**, which MySQL does not
   support — Django drops them silently, so two patients could share an MRN. This
   needs an app-level guard adding before MySQL is safe. SQLite has no such issue.
