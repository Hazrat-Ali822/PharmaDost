# Deploying PharmaDost to cPanel + Passenger (LiteSpeed / CloudLinux)

This is the shared-hosting path — any cPanel host with **Setup Python App**
(the CloudLinux "Python Selector", used on LiteSpeed hosts too). It runs Django
through Passenger and uses **SQLite** — a single file, no database server to
configure, and it sidesteps the one MySQL limitation this app has (conditional
unique indexes for MRN, which MySQL silently drops).

> Before buying the plan, confirm cPanel has **Setup Python App**, **Python 3.10+**,
> and **SSH / Terminal access**. A PHP-only plan cannot run Django.

**Hosting alongside other sites is fine.** Each "Setup Python App" is its own
isolated virtualenv and domain mapping, so PharmaDost on `sehatyar.online` and an
unrelated site on another domain (e.g. `khabirconsultant.ae`) coexist without
touching each other. Put the app in its own folder, **not** in `public_html`.

## 1. Upload the code

Either `git clone` in the Terminal, or upload a zip via File Manager and extract
it. Put it in a folder like `/home/<cpaneluser>/pharmadost` (e.g.
`/home/sehatyar/pharmadost`) — **outside** `public_html`; Passenger maps the
domain to it for you.

## 2. Create the Python app

cPanel → **Setup Python App** → Create:
- Python version: **3.10+**
- Application root: `pharmadost` (the folder from step 1)
- Application URL: your domain
- Application startup file: `passenger_wsgi.py` (already in the repo)

Create it. cPanel makes a virtualenv and shows the command to activate it
(`source /home/<cpaneluser>/virtualenv/pharmadost/3.10/bin/activate`).

## 3. Install dependencies

In the SSH terminal, activate the virtualenv (command from step 2), then:

```bash
cd ~/pharmadost
pip install -r requirements.txt
```

SQLite needs no driver. (Only if you later switch to MySQL: `pip install mysqlclient`.)

## 4. Create `.env` in the project root

```bash
cd ~/pharmadost
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

The SQLite file is created at `~/pharmadost/db.sqlite3`. **Back this file up** — it
is the whole database.

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
cd ~/pharmadost && git pull
source ~/virtualenv/pharmadost/3.10/bin/activate
pip install -r requirements.txt          # only if requirements changed
python manage.py migrate                 # only if there are new migrations
python manage.py collectstatic --noinput # only if static changed
# then: Setup Python App → Restart
```

## Cron (daily alerts)

cPanel → **Cron Jobs**, one daily line (adjust the path):

```
cd ~/pharmadost && ~/virtualenv/pharmadost/3.10/bin/python manage.py expiry_alert && ~/virtualenv/pharmadost/3.10/bin/python manage.py low_stock_alert
```

## If you insist on MySQL

MySQL works but two things must be handled first, so tell the maintainer before
switching:
1. `pip install mysqlclient`, and set `DATABASE_URL=mysql://user:pass@localhost/dbname` in `.env`.
2. The MRN uniqueness uses **conditional unique indexes**, which MySQL does not
   support — Django drops them silently, so two patients could share an MRN. This
   needs an app-level guard adding before MySQL is safe. SQLite has no such issue.
