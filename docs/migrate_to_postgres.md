# Moving the hosted site from SQLite to PostgreSQL

JabraHost offers PostgreSQL (cPanel → Databases → *PostgreSQL Databases*), and the
codebase already runs on Postgres — local development uses Supabase, and
`settings.py` picks the engine up from `DATABASE_URL` with no code change. So the
risk here is **not** the code. It is the data transfer, and the way that transfer
fails is quietly: a model that did not load leaves the site working, the screens
you happen to open look right, and the missing rows surface weeks later when
somebody asks for a patient who is not there.

Everything below is built around catching that.

## Why bother

The hosted site is one `db.sqlite3` file holding every tenant. That means:

- **One writer.** SQLite locks the whole file to write. WAL mode
  (`saas.signals._tune_sqlite`) lets readers carry on, but two receptionists
  billing at the same moment still queue. It holds today; it will not hold at
  15–20 busy tenants.
- **One file to lose.** Corruption or a bad disk takes every customer at once.
- **No per-tenant separation at the storage layer.** This is exactly why the
  one-click backup could hand any tenant admin every other tenant's database
  (fixed — see `can_download_raw_backup`).

Migrating while the data is small is far easier than migrating when it is not.

## Before you start

- Do it at night. The site is down for the duration (10–30 minutes).
- `psycopg2-binary` is already in `requirements.txt`.
- You need the DB name, user and password from the cPanel PostgreSQL wizard.
  cPanel prefixes them with the account name, e.g. `sehatyar_prod` /
  `sehatyar_dbuser`.

## Steps

### 1. Create the database

cPanel → **PostgreSQL Database Wizard**: create the database, create a user, and
grant that user **all privileges** on it. Write the three values down.

### 2. Count what you have

```bash
cd ~/sehatyar
source ~/virtualenv/sehatyar/3.10/bin/activate
python manage.py db_snapshot --save ~/before-counts.json
```

This is the number you will check against at the end. Do not skip it — it is the
only thing that will tell you whether the move lost anything.

### 3. Back up, then dump the data

```bash
cp db.sqlite3 ~/db-before-postgres.sqlite3          # keep this until you are sure

python manage.py dumpdata \
  --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission \
  --exclude sessions.session --exclude admin.logentry \
  --indent 2 -o ~/alldata.json
```

The exclusions matter. `contenttypes` and `auth.permission` are rebuilt by
`migrate` on the new database; loading the old ones on top collides on their
unique constraints and the whole load aborts. Sessions and admin log entries are
disposable — carrying them over only signs people in against stale rows.

### 4. Point the app at PostgreSQL

Add one line to `~/sehatyar/.env` (there is deliberately no `DATABASE_URL` there
today, which is why it falls back to SQLite):

```
DATABASE_URL=postgres://USER:PASSWORD@localhost:5432/DBNAME
```

If the password contains `@ : / #`, percent-encode it or the URL parses wrongly.

### 5. Build the schema and load the data

```bash
python manage.py migrate                 # creates every table, empty
python manage.py loaddata ~/alldata.json
```

If `loaddata` reports an integrity error, **stop**. Remove `DATABASE_URL` from
`.env` and the site is back on SQLite exactly as it was — nothing has been lost.
Fix the reported model and try again.

### 6. Check every row arrived

```bash
python manage.py db_snapshot --compare ~/before-counts.json
```

It prints any model whose count changed and exits non-zero. **Do not go live
until this says every model matches.**

### 7. Restart and test properly

cPanel → Setup Python App → **Restart**.

Then check by hand, because a row count does not prove the app works:

- sign in as a tenant admin, and as the SaaS owner
- open the dashboard — the revenue figures should match yesterday's
- register a patient (this exercises the MRN counter, which locks a row)
- take a POS sale (stock deduction under a lock)
- raise and pay an invoice (the invoice-number counter)

The two counters are the things to watch. Django's `loaddata` resets Postgres
sequences at the end, but `SiteSettings.mrn_last_number` and
`invoice_last_number` are **ordinary integer columns this app maintains itself**,
not sequences — they come across with the data and need no resetting. If a new
patient somehow gets MRN 1, check `SiteSettings` rather than the database.

## If it goes wrong

Remove `DATABASE_URL` from `.env` and restart. The SQLite file is untouched and
the site is exactly as it was. Keep `~/db-before-postgres.sqlite3` for a week
regardless.

## Afterwards

- **Backups change.** `backup_download` refuses on Postgres, and says why: the
  database is no longer a file you can zip. Use `pg_dump` or cPanel's own backup:
  ```bash
  pg_dump -U USER -d DBNAME -F c -f ~/backups/db-$(date +%F).dump
  ```
  Put that on the daily cron line with the alert commands.
- `saas.signals._tune_sqlite` becomes a no-op on its own — it checks the vendor.
- The **desktop / LAN build is unaffected** and stays on SQLite. It sets no
  `DATABASE_URL`, and it should not: one clinic, one machine, no server.
