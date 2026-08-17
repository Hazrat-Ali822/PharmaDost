# Moving the hosted site from SQLite to PostgreSQL

JabraHost offers PostgreSQL (cPanel → Databases → *PostgreSQL Databases*), and the
codebase already runs on Postgres — local development uses Supabase, and
`settings.py` picks the engine up from `DATABASE_URL` with no code change. So the
risk here is **not** the code. It is the data transfer, and the way that transfer
fails is quietly: a model that did not load leaves the site working, the screens
you happen to open look right, and the missing rows surface weeks later when
somebody asks for a patient who is not there.

Everything below is built around catching that. **This procedure has been
rehearsed end to end** — seeded database → export → fresh empty database → import
→ row-count comparison — which is how the encoding trap in step 4 was found. The
obvious `dumpdata -o` command fails on this app's data, every time.

## Why bother

The hosted site is one `db.sqlite3` file holding every tenant. That means:

- **One writer.** SQLite locks the whole file to write. WAL mode
  (`saas.signals._tune_sqlite`) lets readers carry on, but two receptionists
  billing at the same moment still queue. It holds today; it will not hold at
  15–20 busy tenants.
- **One file to lose.** Corruption or a bad disk takes every customer at once.
- **No separation at the storage layer.** This is exactly why the one-click
  backup could hand any tenant admin every other tenant's database (fixed — see
  `can_download_raw_backup`).

Migrating while the data is small is far easier than migrating when it is not.

## Before you start

- Do it at night. The site is down for the duration (10–30 minutes).
- `psycopg2-binary` is already in `requirements.txt`.
- You need a DB name, user and password from the cPanel PostgreSQL wizard.
  cPanel prefixes them with the account name, e.g. `sehatyar_prod` /
  `sehatyar_dbuser`.
- **Rollback is one line** — deleting `DATABASE_URL` from `.env` puts the site
  back on the untouched SQLite file. That is what makes this safe to attempt.

## Steps

### 1. Create the database

cPanel → **PostgreSQL Database Wizard**: create the database, create a user, and
grant that user **all privileges** on it. Write the three values down.

### 2. Check the data will be accepted

```bash
cd ~/sehatyar
source ~/virtualenv/sehatyar/3.10/bin/activate
python manage.py db_preflight
```

SQLite is loosely typed and has not always enforced foreign keys, so a database
that has worked for years can still hold rows PostgreSQL will refuse — a value
longer than its column, a NULL in a NOT NULL column, a foreign key pointing at a
row that was deleted. Without this you find them one stack trace at a time, at
night, with the site down. It only reads; it changes nothing.

Fix anything it reports before going further.

### 3. Count what you have

```bash
python manage.py db_snapshot --save ~/before-counts.json
```

This is the number you check against at the end. Do not skip it — it is the only
thing that will tell you whether the move lost anything.

### 4. Back up, then export the data

```bash
cp db.sqlite3 ~/db-before-postgres.sqlite3          # keep this until you are sure

python manage.py db_export ~/alldata.json
```

**Use `db_export`, not `dumpdata -o`.** Django writes the `-o` file in the
machine's locale encoding, but `loaddata` always decodes UTF-8. This app puts em
dashes into ordinary data — "OPD Consultation — Dr. Sara Ahmed", "Delivery —
Normal", "IPD Bed Charges: … — 2 Day(s)" — so on Windows (cp1252) or any host
whose locale is POSIX/C the import dies with

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 76919
```

**part of the way through**, leaving a half-filled database that looks like it
worked. That is not hypothetical — it is what the rehearsal produced, and
`db_snapshot --compare` then reported 75 models sitting at zero. `db_export`
writes an explicit UTF-8 handle and reads the file back to prove it decodes
before reporting success.

It also applies the exclusions for you, which matter: `contenttypes` and
`auth.permission` are rebuilt by `migrate` on the new database, and loading the
old rows on top collides on their unique constraints and aborts the entire load.
Sessions and admin log entries are disposable.

### 5. Point the app at PostgreSQL

Add one line to `~/sehatyar/.env` (there is deliberately no `DATABASE_URL` there
today, which is why it falls back to SQLite):

```
DATABASE_URL=postgres://USER:PASSWORD@localhost:5432/DBNAME
```

If the password contains `@ : / #`, percent-encode it or the URL parses wrongly.

### 6. Build the schema and load the data

```bash
python manage.py migrate                 # creates every table, empty
python manage.py loaddata ~/alldata.json
```

If `loaddata` reports an error, **stop**. Nothing has been lost — the SQLite
file has not been touched, and if you followed step 5's shell-variable form the
live site is still serving from it.

The new database is now **half full**, so it must be emptied before the next
attempt or the retry collides with what the failed run already inserted:

```bash
python manage.py flush --noinput      # empties every table, keeps the schema
python manage.py loaddata ~/alldata.json
```

**A load can fail on data that is perfectly valid.** `db_preflight` checks the
rows; it cannot see a `post_save` receiver that *writes* while the fixture is
being read. One did, and it stopped the real migration:

```
Could not load user_mgmt.UserProfile(pk=5): duplicate key value violates
unique constraint "user_mgmt_userprofile_user_id_key"
DETAIL:  Key (user_id)=(4) already exists.
```

`user_mgmt.create_profile` made a profile for every user the fixture inserted,
taking the primary keys the fixture's own profiles were meant to land on. Django
passes `raw=True` to signals during a fixture load for exactly this reason, and
the receiver now returns on it. If you ever add a receiver that creates rows,
guard it the same way — `saas.tests_snapshot.FixtureRoundTripTest` dumps and
reloads a small fixture and will catch the next one.

### 7. Check every row arrived

```bash
python manage.py db_snapshot --compare ~/before-counts.json
```

It prints any model whose count changed and exits non-zero. **Do not go live
until this says every model matches.**

### 8. Restart and test by hand

cPanel → Setup Python App → **Restart**.

A row count does not prove the app works, so check:

- sign in as a tenant admin, and as the SaaS owner
- open the dashboard — the revenue figures should match yesterday's
- register a patient (exercises the MRN counter, which locks a row)
- take a POS sale (stock deduction under a lock)
- raise and pay an invoice (the invoice-number counter)

Those two counters are what to watch. `loaddata` resets Postgres sequences at the
end, but `SiteSettings.mrn_last_number` and `invoice_last_number` are **ordinary
integer columns this app maintains itself**, not sequences — they come across
with the data and need no resetting. If a new patient somehow gets MRN 1, look at
`SiteSettings`, not at the database.

## If it goes wrong

Remove `DATABASE_URL` from `.env` and restart. The SQLite file is untouched and
the site is exactly as it was. Keep `~/db-before-postgres.sqlite3` for a week
regardless.

## Afterwards

- **Backups change.** `backup_download` refuses on PostgreSQL and says why: the
  database is no longer a file it can zip, and a zip of media alone labelled as a
  backup is worse than none. Use `pg_dump`:
  ```bash
  pg_dump -U USER -d DBNAME -F c -f ~/backups/db-$(date +%F).dump
  ```
  Put that on the daily cron line with the alert commands.
- `saas.signals._tune_sqlite` becomes a no-op on its own — it checks the vendor.
- The **desktop / LAN build is unaffected** and stays on SQLite. It sets no
  `DATABASE_URL`, and it should not: one clinic, one machine, no server.
