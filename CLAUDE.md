# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is a **multi-tenant SaaS Hospital & Pharmacy Management System** built with Django 4.2 (Python 3.10 locally, 3.13 on the production host). Server-rendered Django templates + vanilla JS — no frontend build step, no SPA framework.

**Brand vs codebase name.** The product ships as **Sehatyar** (`sehatyar.online`) — that is the name in every user-facing string and the platform default brand (`user_mgmt/models.SiteSettings` DEFAULTS). The *codebase* keeps its original name: the repo is `PharmaDost`, the Django project package is **`pharma_mgmt`** (settings/urls/wsgi live there — do NOT rename it; it is invisible to users and every `--settings=pharma_mgmt...` import depends on it). So "PharmaDost"/`pharma_mgmt` in code and docs is expected; user-facing text must read "Sehatyar". Each tenant then overrides the brand with its own name/logo/colour via `SiteSettings`.

One deployment serves many hospitals ("tenants"). The same codebase is also packaged as a **local Windows desktop app** (PyInstaller + waitress + SQLite), which is why data paths are indirected through `DATA_DIR`. That build doubles as a **clinic LAN server** — it binds `0.0.0.0`, so every phone on the same wifi runs the whole system with no internet at all (see "The desktop build as a clinic LAN server").

## Commands

```bash
# Dev server
python manage.py runserver

# Tests — ALWAYS pass the test settings (see "Two databases" below)
python manage.py test --settings=pharma_mgmt.test_settings
python manage.py test inventory --settings=pharma_mgmt.test_settings          # one app
python manage.py test ipd.tests_workflow.NurseRoleTest --settings=pharma_mgmt.test_settings   # one class
python manage.py test inventory.tests_modern.AnalyticsTest.test_dead_stock_and_movers --settings=pharma_mgmt.test_settings

python manage.py check
python manage.py makemigrations <app>
python manage.py migrate
```

There is no linter or formatter configured in this repo.

### Testing

Test tooling lives in `requirements-dev.txt` (`pip install -r requirements-dev.txt`).

| Layer | Where | Notes |
|---|---|---|
| Unit / integration / functional | each app's `tests*.py` | Django `TestCase` + test client |
| Smoke | `tests/test_smoke.py` | opens 60+ pages as an admin; fastest way to catch a broken template or `{% url %}` |
| Security | `tests/test_security.py` | auth, tenant isolation, fail-closed, authorisation, CSRF, credentials |
| Admin visibility | `tests/test_admin_awareness.py` | audit-log tenant isolation, overview counters, what the owner is notified about |
| End-to-end | `e2e/test_e2e.py` | real Chromium via Playwright; **skips itself** when Playwright or its browser is absent |
| Performance | `tests/test_performance.py` | query-count ceilings on hot paths; catches N+1s |
| Load / performance | `loadtest/locustfile.py` | Locust, run manually against a disposable instance |

```bash
# coverage (config in .coveragerc; ~63% at last measure)
coverage run manage.py test <apps...> --settings=pharma_mgmt.test_settings
coverage report -m
coverage html                                    # open htmlcov/index.html

# end-to-end, one-time browser download
playwright install chromium
python manage.py test e2e --settings=pharma_mgmt.test_settings

# load test — NEVER against production; the counter task posts real sales
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 50 -r 5 -t 2m

# security scans (also run in CI)
python manage.py check --deploy
bandit -r . -x ./.venv,./e2e,./loadtest,./tests,./staticfiles,./desktop -ll
pip-audit --requirement requirements.txt
```

`.github/workflows/ci.yml` runs the suite, a missing-migration check, coverage, the E2E
job and the security scans on every push and pull request.

**Testing offline behaviour needs the network genuinely severed**, and three obvious ways
of doing that silently do nothing — each one leaves the test passing while proving nothing:

- `context.set_offline(True)` and `context.route(..., abort)` apply to the *page*, not to
  the service worker's own fetches; the worker keeps reaching the server.
- `server_thread.terminate()` closes the listening socket but leaves the keep-alive
  connections Chrome already holds being served by their handler threads.

`e2e.TrulyOfflineTest` therefore sets `QuietWSGIRequestHandler.protocol_version` to
`HTTP/1.0` (no keep-alive, so every request needs a fresh socket) and then stops the server
— and asks for an **un-cached URL first as a control**. The app's own offline page coming
back is what proves the cut worked; Django's 404 coming back means it did not. Keep that
control: without it the whole test is decoration. The class holds one test because there is
no server left to run a second against.

Two traps when adding tests:

- `Patient.mrn` is auto-allocated when left blank, so fixtures normally omit it. Passing
  one explicitly is still fine (`seed_demo` does, to find its own rows again) and does not
  consume a number from the sequence.
- `LiveServerTestCase` (so all of `e2e/`) extends `TransactionTestCase`, which does **not**
  run `setUpTestData`. Build fixtures in `setUp`, or the tests hit the first-run setup
  wizard instead of the app.

### Management commands

| Command | Purpose |
|---|---|
| `seed_demo` | Demo hospital + users + data (all demo passwords are `pharma123`) |
| `seed_public_demo [--reset]` | Isolated **public demo tenant** ("Sehatyar Demo Hospital", slug `demo`) with a user per role (all `demo1122`) and data in **every** module. Idempotent — re-run only refreshes logins; `--reset` wipes and rebuilds. Powers the `/demo/` one-click login. |
| `expiry_alert [--days N]` | Notify pharmacist/admin about near-expiry stock (daily cron) |
| `low_stock_alert` | Notify pharmacist/admin about low stock (daily cron) |
| `reconcile_stock [--fix]` | Repair `Medicine.quantity` drift vs the sum of its `StockBatch` rows (weekly cron) |
| `repair_tenant_orphans` | Fix rows left with `hospital = NULL` |
| `seed_lab`, `import_labs_scans`, `seed_org`, `seed_roles`, `seed_icd`, `seed_epi` | Catalog/role seeding (`seed_icd` = ICD-10 codes, `seed_epi` = EPI vaccine schedule; both global & idempotent) |

## Two databases — do not conflate them

This is the single most common source of confusion:

- **Local dev** uses **Supabase PostgreSQL**, via `DATABASE_URL` in `.env`.
- **Production (JabraHost — cPanel + Passenger)** uses **local SQLite** at `~/sehatyar/db.sqlite3`. There is deliberately no `DATABASE_URL` in the host's `.env`, so `settings.py` falls back to its SQLite default. The `WARNING:root:No DATABASE_URL environment variable set` line in command output on that host is benign and expected. (An older PythonAnywhere install used `/home/PharmaDost/PharmaDost/db.sqlite3` and its own WSGI file; the live site is the cPanel one — `passenger_wsgi.py` + `docs/deploy_jabrahost.md`.)

They are **separate databases**. A migration applied locally is *not* applied in production; it must be run again on the host.

`settings.py` resolves the DB as: SQLite at `DATA_DIR/db.sqlite3` by default, overridden by `DATABASE_URL` (via `dj_database_url`) when present. `.env` is loaded from `BASE_DIR` first, then `DATA_DIR` with `override=True` (the desktop app sets `PHARMADOST_DATA_DIR` to a writable per-user folder).

Tests run against in-memory SQLite via `pharma_mgmt/test_settings.py` because the remote Postgres is too slow and flaky for a test suite. Omitting `--settings=pharma_mgmt.test_settings` will run the suite over the network and may hang.

## Deploying to JabraHost (cPanel + Passenger)

Full first-time setup — creating the Python app, `.env`, the owner superuser — is
`docs/deploy_jabrahost.md`. The routine update, over SSH:

```bash
cd ~/sehatyar
cp db.sqlite3 "backups/db-$(date +%F-%H%M).sqlite3"    # before ANY migrate; SQLite is one file
git pull
source ~/virtualenv/sehatyar/3.10/bin/activate
pip install -r requirements.txt           # only if requirements.txt changed
python manage.py migrate                  # only if there are new migrations
python manage.py collectstatic --noinput  # only if static/ changed — templates are NOT static
# then: cPanel → Setup Python App → Restart (required; nothing takes effect without it)
```

Paths assume the app root is `~/sehatyar` and the cPanel virtualenv is
`~/virtualenv/sehatyar/3.10/` — adjust to the real ones on the account.

**Activate the virtualenv first, every time.** A bare `python` outside it is the system
interpreter and fails with `ModuleNotFoundError: dj_database_url`. Either `source
~/virtualenv/.../bin/activate` or call the full path
`~/virtualenv/sehatyar/3.10/bin/python`.

**Restart is not optional.** Passenger keeps the old process alive until it is restarted,
so a `git pull` alone changes nothing a visitor can see. (`touch tmp/restart.txt` in the
app root does the same thing from the shell.)

**`DJANGO_SECRET_KEY` must be in `.env` on the host.** Anything under `/home/` refuses to
start on the built-in default — that key signs session cookies, so a server using the
published one can be logged into as any user by anyone. Set it once:

```bash
cd ~/sehatyar
python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64))" >> .env
```

Changing it signs everyone out once. Do **not** gate this on `DJANGO_ENV` — nothing sets
that variable on the host, which is why the old check never fired.

Daily alerts run from cPanel → Cron Jobs as a single chained line (see the deploy doc).

The free tier allows only **one** scheduled task, so the cron commands are chained into a single daily line in the Tasks tab.

## Architecture

### Multi-tenancy (read this before touching any query)

Tenancy is **opt-in per model** and enforced in three cooperating places:

1. `saas.middleware.TenantMiddleware` reads `request.user.hospital` and stores it in a **thread-local** (`saas/utils.py`).
2. `saas.utils.TenantManager` — set as `objects` on a tenant model — filters `get_queryset()` by the current hospital.
3. `saas/signals.py` registers a **global** `pre_save` receiver (no `sender`) that stamps `instance.hospital` from the thread-local on any model that has a `hospital` field and hasn't set it.

A model is only isolated if it has **both** a `hospital` FK **and** `objects = TenantManager()`. Several apps still lack this; check before assuming a queryset is scoped.

`TenantManager` resolves in three steps: a bound hospital filters to it; otherwise, if the
thread is **strict** (`TenantMiddleware` sets this for every authenticated non-superuser) it
filters to `hospital IS NULL`; otherwise it returns the queryset unfiltered. That last case
is deliberate — management commands, cron jobs and the superuser SaaS portal operate across
all tenants.

The strict flag exists because the manager used to be fail-**open**: a logged-in user whose
`hospital` was `None` fell through the filter and read *every* tenant's patient records.
Do not "simplify" `TenantManager` or `TenantMiddleware` back to a bare `if hospital:` —
`tests/test_security.py::FailClosedTest` guards this.

Models without a `hospital` column (`Doctor`, `Appointment`, `Prescription`, `TestOrder`,
`ImagingStudy`, and line-item models) get no protection from the manager at all. They are
scoped **only** by the view-level helpers, so those helpers are load-bearing. Scope
fail-closed — key on superuser, never on "does this user have a hospital":

```python
# correct — a hospital-less non-superuser matches only hospital-less rows
if not request.user.is_superuser:
    qs = qs.filter(patient__hospital=request.user.hospital)

# WRONG — a user whose hospital is None sees every tenant's data
if request.user.hospital:
    qs = qs.filter(patient__hospital=request.user.hospital)
```

Apps follow this with module-local helpers: `_scoped_prescriptions` / `_scoped_appointments` / `_scoped_presets` (prescriptions), `_scoped_orders` (lab), `_scoped_studies` (imaging), `_scoped_admissions` (ipd), `_get_scoped_patient` (patients). Reuse them rather than re-rolling the filter — in **list** views too, not just detail views: `lab.order_list`, `imaging.study_list` and `opd.appointment_list` once filtered on the fail-**open** `if request.user.hospital:` form and leaked every tenant's clinical records to a hospital-less non-superuser (the sibling detail views were already fail-closed). `RxPreset` has a `hospital` FK but **no `TenantManager`**, so its edit/delete-by-pk paths must go through `_scoped_presets` or they are cross-tenant writes. `tests/test_security.py::FailClosedTest` now covers all three lists — keep it that way. The sidebar badge counts in `accounts/context_processors.py` use the same `scope_by_hospital = not user.is_superuser` flag.

On top of tenant scoping, a **doctor is narrowed to their own patients**: lab orders they
placed, imaging they referred, and admissions where they are the `attending_doctor` **or**
raised the `AdmissionRequest` (reception may allot a different attending doctor, but the
doctor who asked for the bed keeps the patient). Admin, reception and nurses are not
narrowed — they need the whole ward to work. `Admission` has a `hospital` FK and
`TenantManager`, so `_scoped_admissions` adds only the clinical narrowing.

### Permissions: modules × features × roles

Single source of truth is `accounts/permissions.py`. Three layers stack:

- **`FEATURES`** — `feature_key -> set of roles` that get it by default. This is the per-user layer.
- **`MODULES`** — business-level on/off bundles (`pharmacy`, `opd`, `ipd`, `ot`, `lab`, `imaging`, `finance`, `reports`), each mapping to feature keys. Chosen in the setup wizard or Settings, stored on `Hospital.enabled_modules` / `SiteSettings.enabled_modules` (null = all on). `CORE_FEATURES` are always on.
- **`User.custom_features`** (JSONField) — per-user override. `None` = inherit role defaults; a list = exactly that set (even `[]`).

Access is granted only when the feature is **both** installed for the tenant **and** held by the user:

- `accounts.decorators.feature_required(*keys)` gates views (passes on ANY key); `role_required([...])` remains for fine sub-gates.
- `accounts.context_processors.nav_permissions` builds the `nav` dict for the sidebar from the *same* helpers.

**Keep these in sync.** If you gate a view on a feature, gate its nav link and any button that links to it on the matching `nav.<key>`, or users get a link straight into a 403.

Roles: `ADMIN`, `RECEPTIONIST`, `DOCTOR`, `NURSE` (Ward Staff), `PHARMACIST`, `WHOLESALE`, `LABTECH`, `SONOGRAPHER`, `ACCOUNTANT`.

The `ward` feature (nurses) is deliberately narrower than `ipd`: ward views take
`feature_required('ipd', 'ward')`, while admitting, discharging and ward setup stay
`ipd`-only. `lab.order_create` and `imaging.study_create` also accept `ward` so the ward
can raise an order for an admitted patient — entering results or writing reports remains
role-gated to lab/radiology.

Doctors hold `ipd`, so the IPD sidebar link is shown to them as **"My Inpatients"** and
lands on the same `admission_list`, narrowed by `_scoped_admissions`. Buttons that need
the full `ipd` feature (Admit Patient, Discharge) are gated on `nav.ipd` in
`templates/ipd/admission_list.html` so a nurse never gets a link into a 403; "Admit
Patient" is additionally hidden from doctors, who advise instead of admitting.

**`ward_manage` (Ward In-charge / Charge Nurse)** is a third tier above `ward`. `ward`
(every nurse) can *view* the duty roster and their own duties and do bedside work;
`ward_manage` *builds* the roster and *allocates* patients. Default roles: `ADMIN` only —
the admin promotes a senior nurse to In-charge by granting `ward_manage` in the access
editor. It is bundled into the `ipd` module. Screens (`ipd/views.py`): `duty_roster`
(`ward`, weekly grid per ward, one `NurseShift` per nurse/date/shift — the roster the
In-charge builds), `patient_allocation` (`ward_manage`, assigns each admitted patient to a
nurse *rostered for that shift* — `PatientAllocation` is unique per admission/date/shift,
and the nurse's load is shown so the ratio is visible), and `my_duties` (`ward`, a nurse's
own upcoming shifts + the patients allotted to them today). `Ward.in_charge` names the
senior nurse who runs the ward. Shifts are `MORNING/EVENING/NIGHT` (`ipd.models.SHIFT_CHOICES`,
times in `SHIFT_TIMES`); `_current_shift()` maps the clock to one. `roster_add` upserts on
the unique `(nurse, date, shift)` so a re-add moves the nurse rather than duplicating.

**Nursing vitals + MEWS + fluid balance** (the nurse's continuous-care layer, distinct from
`DoctorRound`). `VitalsObservation` is the nursing TPR chart — it carries the parameters a
temp/pulse/BP row omits (respiratory rate, SpO₂, AVPU consciousness, pain, blood glucose),
all nullable so a partial set still saves. `ipd.models.compute_mews(...)` scores a **MEWS**
off temp (taken in °F, converted to °C), pulse, RR, systolic BP and AVPU — each 0–3; band is
GREEN (0–1) / AMBER (2–3) / RED (≥4 or any single 3), with escalation advice. RR uses NEWS2
bands (12–20 → 0). `VitalsObservation.mews` is a derived property (never stored), so a
threshold change re-scores old rows. `FluidBalanceEntry` is the intake/output chart;
`fluid_totals(admission, date)` gives in/out/balance. Both are recorded by nurses
(`feature_required('ipd','ward')`) through `vitals_add` / `fluid_add`, which reuse
`ipd.services.record_vitals` / `record_fluid` — the same path the offline `vital` / `fluid`
handlers call, so both forms are offline-capable (bedside round with no signal), carrying
`admission_id` as a hidden input. `nursing_board` (`/ipd/board/`) lists every inpatient with
their latest MEWS, whether obs are overdue (`OBS_INTERVAL_HOURS`, default 6) and this shift's
allocated nurse, sorted sickest-first — the In-charge's "who needs attention now".

**Nursing records, handover and census** (the documentation + shift-change layer).
`NursingNote` is the nurse's own shift progress note (narrative, offline kind `nursing_note`),
kept separate from `DoctorRound` because a ward note is a record in its own right. `CareTask`
logs routine care — turning, hygiene, catheter/IV/wound care, mobilisation, feeding (offline
`care_task`) — the chart a vitals/med row doesn't hold. `ShiftHandover` is a per-patient
**SBAR** (Situation/Background/Assessment/Recommendation) written at end of shift (offline
`handover`); `handover_board` (`/ipd/handover/`) lists every inpatient's latest handover for
the incoming nurse, unacknowledged first, and `handover_ack` records who took over — that
acknowledgement is online-only (it writes against live state). All three recording forms
reuse `ipd.services.record_nursing_note` / `record_care_task` / `record_handover` and carry
`admission_id`. `ward_census` (`/ipd/census/`, `ward_census(qs, date)` in models) is the
daily census — admissions, discharges and occupancy for a date, per ward and hospital-wide,
computed from the admission records (no snapshot model). All of these gate on
`feature_required('ipd','ward')`; the census link is `nav.ipd` only.

### What the admin is told

Two channels, deliberately separated — mixing them makes the inbox unreadable:

- **`user_mgmt/overview.py`** builds the admin dashboard: today's counters, an
  "attention" list (unpaid bills, low/expired stock, waiting queues, failed logins), who is
  in OPD, and the recent audit feed. Routine traffic lives here. Zero counts are dropped
  rather than shown, so the list stays short enough to read.
- **`Notification.notify_admins()`** is for the *exceptional* only — currently stock written
  off (`inventory.adjustment_create`, negative adjustments only), an invoice voided
  (`billing.invoice_void`), and a run of failed sign-ins. Do not add routine events here:
  an inbox that fills with normal activity is one nobody reads.

Repeated failed sign-ins fire once, on the attempt that crosses `FAILED_LOGIN_BURST` within
`FAILED_LOGIN_WINDOW_MINUTES` (`audit/signals.py`) — not on every attempt after it.

`Notification.save()` is the single chokepoint for every creation path, and it is where the
offline-replay stamp is applied (see "Offline data entry"). Keep new notification code
going through `objects.create` / `send_to_role` rather than `bulk_create`, which skips
`save()` and would put a replayed queue back to ringing once per entry.

**`AuditLog` is tenant-scoped** (`hospital` FK + `TenantManager`, with `all_objects` for the
superuser portal and commands). It was not, and one hospital's admin could read every other
tenant's trail — patient names, sales, staff sign-ins — from `/audit/`. Entries are filed
under the hospital the *affected object* belongs to, not the actor's, so a superuser editing
a tenant's record still shows up for that tenant's admin. Failed sign-ins are filed by
looking the attempted address up; without that they land under no hospital and the admin
whose staff account is being guessed at never sees them. The filter dropdowns are built
from the scoped queryset too — a list naming another tenant's staff leaks just as surely as
the rows. Guarded by `tests/test_admin_awareness.py::AuditLogIsolationTest`.

**Deleting a whole tenant suppresses audit logging** (`audit.middleware.suppress_audit`).
The SaaS owner can hard-delete a hospital (`saas.views.hospital_delete`, superuser-only,
type-the-name confirmation). Every tenant model's `hospital` FK is `CASCADE`, but a bare
`hospital.delete()` **still fails on any tenant that has traded** — child rows hold `PROTECT`
FKs to their parents (`SaleItem`→`Medicine`/`StockBatch`, `Invoice`→`Patient`, `Sale`→
`Customer`/`Panel`, `EmergencyCase`/`Pregnancy`/`Delivery`→`Patient`, `VaccinationRecord`→
`Vaccine`, `StockAdjustment`→`StockBatch`, …), so the cascade raises `ProtectedError`. Both
the delete view **and** the demo `--reset` (`seed_public_demo._reset`) therefore call
**`saas.services.purge_tenant(hospital)`** first: it deletes every hospital-scoped row in
repeated passes — each pass removes the rows no longer protected (a `Sale` carries its
`SaleItem`s off by cascade, unprotecting the `Medicine`), unblocking their parents next pass —
converging with **no hard-coded order**, so a new model with a `hospital` FK is handled
automatically. It skips `User` and `Hospital` for the caller. Then `hospital.delete()` on the
now-childless row, and **`User.hospital` (`SET_NULL`)**: the view captures the tenant's
non-superuser staff ids first and deletes them after, or they linger as orphaned accounts that
can still sign in. The whole thing runs inside `suppress_audit()` because the deletes'
`post_delete` signals would otherwise file a DELETE `AuditLog` for every tracked row against
the very hospital being removed — rows that `AuditLog.hospital`'s own CASCADE deletes again,
and which crash on save as the hospital vanishes. Guarded by `saas/tests_delete.py`
(`test_delete_tenant_that_has_traded` seeds the protecting rows — do not drop it, or the
regression is unguarded again).

**Subscription / renewal (SaaS owner portal).** A tenant is gated by `Hospital.expiry_date`
+ `is_active`: `saas.middleware.HospitalSubscriptionMiddleware` shows `saas/suspended.html`
once expired/inactive (and sets `request.subscription_warning` at ≤5 days, rendered as a
banner in `base.html`). The middleware **skips superusers and hospital-less users** — which is
why the **desktop/LAN build never expires**: its first-run admin is a superuser and the
install is hospital-less, so no subscription check ever fires. Only hosted tenants (a
hospital-scoped user) are gated. Renewal is one action: `saas.views.hospital_renew` extends
`expiry_date` by N months (adding on top of time left when renewing early, from today when
already expired), reactivates a suspended tenant, and **records a `HospitalPayment` in the same
step** so the history builds itself — then redirects to `payment_invoice` (a printable
receipt extending `print/base_print.html`, platform-branded, currency literal `Rs`).
`hospital_detail` shows status + the full renewal history; the dashboard links each tenant to
it with a Renew button. Guarded by `saas/tests.py::SubscriptionRenewalTest`.

### Login front door (host-aware) — who may sign in where

The `/login/` route is `accounts.views.smart_login`, which dispatches **by host**:

- **A hospital subdomain** (`<slug>.<BASE_DOMAIN>`, e.g. `shaheen-health-care.sehatyar.online`)
  renders that hospital's **branded, isolated** login (`saas.views.render_hospital_login`) —
  an account belonging to another hospital is rejected there even with a correct password;
  only that tenant's staff (or a superuser) may sign in.
- **The bare platform domain** (`sehatyar.online`) renders `RootLoginView` — **only the SaaS
  owner (a superuser) may sign in**; a hospital's staff who try are not logged in and are shown
  a link to their own hospital portal (`tenant_login_url`). The public demo button is unaffected
  (it goes through `demo_login`, a separate view). This is the deliberate policy: the platform's
  front door belongs to the owner; every tenant has its own address.

Host → tenant resolution is `saas.utils.hospital_from_host` / `subdomain_slug`, keyed off
`settings.BASE_DOMAIN` (env `PHARMADOST_BASE_DOMAIN`, default `sehatyar.online`). It returns
None for the bare domain, `www`, a deeper label, localhost or an IP — so dev/LAN never resolves
a tenant by host. The **path form `/<slug>/login/` still works** (`saas.views.hospital_login`
→ same `render_hospital_login`) as a fallback for hosts without wildcard DNS yet.

**The desktop / LAN build (`settings.DESKTOP_BUILD`) uses a plain `LoginView` instead** — this
is load-bearing and was a shipped bug. That build has no SaaS owner: it is one clinic, reached
at `localhost` or a LAN IP, and **neither resolves a tenant by host**, so `smart_login` would
fall through to the owner-only `RootLoginView` and admit *only* the first-run superuser —
locking out every non-superuser nurse / receptionist / doctor the clinic creates, on the very
phones the LAN build exists to serve. So `smart_login` short-circuits to the plain login (admits
any active user) when `DESKTOP_BUILD` is set, before any host resolution. Guarded by
`saas/tests_login.py::DesktopLanLoginTest` (LAN staff sign in; the hosted bare domain stays
owner-only). The browser E2E suite runs as this single-instance case by force-planting a session
cookie (`e2e.test_e2e.BrowserTestCase.login`) rather than depending on host policy.

Two things are load-bearing:

- **`reverse('login')` resolves to `/accounts/login/`, not `/login/`.** `django.contrib.auth.urls`
  (included for password-reset etc.) also registers a view named `login` at `/accounts/login/`,
  and with duplicate names Django's reverse picks that one — so the login form
  (`action="{% url 'login' %}"`) and `LoginRequiredMiddleware` POST there. `pharma_mgmt/urls.py`
  therefore **shadows `/accounts/login/` with `smart_login` too** (before the auth include), so
  every login path routes through the host-aware view. Removing that shadow silently sends the
  POST to the plain `LoginView`, bypassing all tenant isolation on submit (the bug this fixes).
- Subdomains require **wildcard DNS (`*.<domain>`) and a wildcard TLS cert** on the host to
  resolve in a browser; the app side (wildcard `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`, added
  from `BASE_DOMAIN` in `settings.py`) is ready regardless. Guarded by `saas/tests_login.py`.

### Landing / dashboards

`LOGIN_REDIRECT_URL` → `user_mgmt:post_login_redirect` → `user_mgmt.views.dashboard_router`, which sends superusers to the SaaS portal, ADMINs to `/`, and everyone else to a role template from `ROLE_TEMPLATES`.

**Public demo.** `/demo/` (`accounts.views.demo_login`, in `LoginRequiredMiddleware.ALLOWED_NAMES` so it's reachable signed-out) signs a visitor straight in — no password — as `demo@sehatyar.online`, a non-superuser ADMIN scoped to the isolated demo tenant (`seed_public_demo`). The login page carries a "Try the live demo" button to it. Because the demo user is tenant-scoped and never a superuser, playing in the demo can't touch a real hospital's data. The explicit `path('demo/', ...)` sits above the `<slug:hospital_slug>` catch-all so it wins.

**Public SEO / AEO surface** (`user_mgmt/seo_views.py`). Everything else is behind a login, so a search engine or AI answer engine reaching `sehatyar.online` would see only a sign-in form — nothing to index or cite. Four crawlable, anonymous endpoints fix that: `/features/` (`seo_landing`, a keyword-rich marketing page carrying `SoftwareApplication` + `Organization` + `FAQPage` JSON-LD and OG tags — the page that ranks), `/robots.txt`, `/sitemap.xml`, and `/llms.txt` (the llmstxt.org convention: a plain-language product brief an LLM reads to describe/recommend the system). All four are in `LoginRequiredMiddleware.ALLOWED_NAMES` and are **tenant-free** (they describe the platform, not any hospital); canonical URLs use the bare `BASE_DOMAIN` so a subdomain never competes with the marketing home. Feature/FAQ copy lives once in `seo_views` and feeds the page, the sitemap and llms.txt together. The `/features/` route sits **above** the `<slug:hospital_slug>` catch-all or the slug pattern would swallow it. No ratings are ever fabricated in the JSON-LD. Guarded by `tests/test_seo.py`.

On top of that, **`seo_views.CONTENT_PAGES`** holds keyword-targeted content pages — one per search intent (`/hospital-management-system/`, `/pharmacy-management-software/`, `/sehat-card-billing-software/`, `/clinic-management-software/`) — each with genuine copy, a FAQ, and `WebPage` + `BreadcrumbList` + `FAQPage` JSON-LD (`seo_views.content_page` + `templates/seo/content_page.html`). Their slugs are top-level URLs, so `pharma_mgmt/urls.py` registers them (looping over `CONTENT_PAGES`) **above** the `<slug:hospital_slug>` catch-all; their view name is `seo_page_<slug>`, which `LoginRequiredMiddleware` allows via a `startswith('seo_page_')` check, and each is in the sitemap at priority 0.9. Add a page by adding a `CONTENT_PAGES` entry (including its short `nav` label) — the URL, sitemap, middleware **and the nav links** pick it up automatically.

Those pages had URLs, a sitemap and JSON-LD but **no link from anywhere a person lands**, so only crawlers ever reached them — and search engines discount a page nothing internally links to. **`seo_views.public_pages()`** builds `[(path, label)]` from `CONTENT_PAGES` itself; the `site_branding` context processor exposes it as `public_pages` (a plain list, no query, so it is safe on every render), and it renders as the strip above the sign-in form (`registration/login.html`) and in the marketing footer (`seo/landing.html`). Building it from `CONTENT_PAGES` rather than writing the links out again is the point: a new page cannot end up unlinked. The strip is hidden on `desktop_build` — the clinic LAN has no internet, so those would be dead links. Guarded by `tests/test_seo.py::PublicPageLinksTest`, which also asserts every linked path actually returns 200. Note the tenant portal (`saas/login.html`) is a **different template** and deliberately carries none of this: a hospital's branded sign-in page is not a place for platform marketing.

The sign-in page itself (`registration/login.html`) is the platform's shop window: a floating dark navbar (`.site-nav`) carrying the logo, those links and a Live Demo button, over a full-bleed background photograph with the sign-in card floating on it. **The picture is one file, `static/img/login-bg.jpg`** — replace it to change the artwork, no template edit. What ships is an abstract medical-tech field in the brand colours (hex grid, node mesh, radar rings and two ECG traces), not a photograph, so nothing is missing if it does not look like one. It is layered *under* a left-to-right dark gradient (heavy on the left where the headline sits, clearing to the right) and *over* the brand gradient, so a missing or slow image degrades to the gradient instead of an unreadable page. The navbar is hidden on `desktop_build` (no internet, dead links), and the hero shows the logo only in that case — otherwise the navbar already carries it.

**The public demo's branding is locked** (`saas.utils.is_demo_hospital`, slug `demo`). `/demo/` signs any visitor in as an ADMIN of that tenant, so without the lock one passer-by's uploaded logo and renamed hospital would greet everybody after them. `user_mgmt.views.site_settings` still *renders* for the demo (it is part of what the demo shows) but refuses the POST. Guarded by `tests/test_seo.py::DemoBrandingLockTest`, which also asserts a real tenant can still save.

`/` maps to **`user_mgmt.seo_views.home`**: a signed-in user is handed straight to `inventory.views.dashboard` (the pharmacy dashboard, unchanged); an **anonymous** visitor on the bare platform domain is served the public marketing landing (so the homepage a crawler/AI indexes is real content, not a login wall), while an anonymous visitor on a tenant subdomain — or any anonymous request on the desktop/LAN build — is redirected to `login`. `'dashboard'` (the root) is therefore in `LoginRequiredMiddleware.ALLOWED_NAMES`; the login-walled app dashboard is still reachable at `/dashboard/` (`dashboard_page`). The dashboard itself is **not** feature-gated with a hard 403 — users lacking `inventory` are redirected to `post_login_redirect` instead, and `dashboard_router` avoids bouncing back for admins whose pharmacy module is off. Preserve both sides of that guard or you create a redirect loop.

`dashboard` **also** sends a hospital-less superuser straight to `saas:dashboard`, mirroring the router. `dashboard_router` only runs at login, so typing the bare domain while already signed in skipped it and dropped the SaaS owner onto a tenant's pharmacy dashboard — which reads as "the platform has turned into one hospital". That hop goes directly to `/saas/`, which never redirects back, so it cannot loop; do not route it through `post_login_redirect` instead.

### The front desk (reception → doctor)

`opd/reception/` is where a visit starts. It asks one question — **new patient or old?**

- **New** → `visit_create` renders `PatientForm` *and* `VisitForm` on one screen and one
  submit registers the patient, books the appointment and raises the consultation invoice.
- **Old** → search by MRN, mobile, CNIC or name. CNIC is stored dashed, so the query
  annotates a `Replace`-stripped copy and matches against that; typing the number straight
  off the card has to work.

Both paths land on `appointment_slip` — the printable A4 token slip. Every printed
document (slip, patient bill, lab/imaging report, PO, IPD discharge summary) extends
`templates/print/base_print.html`: one branded letterhead with theme variants, an
`extrastyle` block for page-specific CSS, an `actions` block for extra no-print buttons,
and `@page`/`@media print` geometry. Do not hand-roll a print stylesheet — extend that base.

**Departments** (`opd.Department`, tenant-scoped) come first: reception picks the
department, and only that department's doctors are offered. `VisitForm.clean` rejects a
doctor/department mismatch, so the JS filter is convenience, not the guard.

### Emergency / Casualty

The `emergency` app (`/emergency/`, feature `emergency`, module `emergency`; roles
ADMIN/DOCTOR/NURSE/RECEPTIONIST) is the casualty desk — deliberately lighter than an
OPD appointment or an IPD admission. `EmergencyCase` records a **triage** level
(RED/YELLOW/GREEN/BLACK, `TRIAGE_ORDER` sorts the board sickest-first), chief
complaint, mode of arrival, the **MLC** (medico-legal) flag every police/RTA case
needs, free-text triage vitals, and a **disposition** (waiting → in-treatment →
admitted/discharged/referred/LAMA/expired). Intake (`emergency.services.register_case`)
either takes a registered patient or **quick-registers a new one from a name alone**
(the ER registers first, completes paperwork later), and raises an optional
consultation invoice via `create_service_invoice`. `emergency_board` lists open cases
by triage rank; a disposition that leaves the active set stamps `disposed_at`. Doctors
in the intake dropdown are scoped the app's usual way for the manager-less `Doctor`
model — `Q(user__hospital=...) | Q(user__isnull=True)`. Guarded by `emergency/tests.py`.
Not offline in v1.

### Maternity / Obstetrics

The `maternity` app (`/maternity/`, feature `maternity`; roles ADMIN/DOCTOR/NURSE)
is ANC + deliveries + the birth register. `Pregnancy` is the antenatal record (LMP,
gravida/para/abortions, high-risk flag); **EDD and gestational weeks are derived from
the LMP** (Naegele's rule, LMP + 280 days) and never stored, so a corrected LMP
re-derives them. `AntenatalVisit` is the checkup chart (BP/weight/fundal height/FHR),
its `weeks` derived from the pregnancy LMP. `Delivery` records the event (type, outcome,
who conducted it) and, on save, sets its pregnancy `DELIVERED` and raises an optional
delivery charge; `Birth` is **one row per baby** (twins → two), so the delivery form
posts parallel `baby_sex[]`/`baby_weight[]`/`baby_status[]` arrays and a live delivery
with no baby row still writes one. `birth_register` is the register view. Guarded by
`maternity/tests.py`.

### ICD-10 diagnoses

The `diagnosis` app (`/diagnosis/`, feature `diagnosis` in the `opd` module; DOCTOR/
ADMIN) adds coded diagnoses. `DiagnosisCode` is a **global** reference table (ICD-10 is
an international standard — no `hospital` FK, no `TenantManager`), seeded by
`seed_icd` (idempotent, ~35 common codes) and extendable by an admin on
`/diagnosis/catalogue/` (role-gated to ADMIN even though DOCTOR holds the feature).
`PatientDiagnosis` is the tenant-scoped record linking a patient to a code. Guarded by
`diagnosis/tests.py`.

### Referrals (in / out)

The `referral` app (`/referral/`, feature `referral` in the `opd` module; roles
ADMIN/DOCTOR/RECEPTIONIST) records a patient sent to another facility (OUT — the
common case, and what the printed letter is for) or received from one (IN).
`facility` is always the *other* facility; the letterhead names this hospital.
`referral_letter` extends `print/base_print.html`. Tenant-scoped (`hospital` FK +
`TenantManager`); nothing global. Guarded by `referral/tests.py`.

### Birth & Death certificates

The `certificates` app (`/certificates/`, feature `certificates` in the `opd`
module; roles ADMIN/DOCTOR/RECEPTIONIST) is the records office. `BirthCertificate`
and `DeathCertificate` are **stand-alone documents** — neither requires a `Patient`
row (a newborn has none; a death may be certified for someone never admitted), so
identity is captured as plain fields (`DeathCertificate.patient` is an optional
SET_NULL link). `serial_no` (`B-00001` / `D-00001`) is a **per-hospital** human
counter allocated in `save()` via `_next_serial` (count-based — these are low
volume, no lock; `all_objects` is the unscoped manager it counts through), so each
tenant numbers its own from 1. Both print sheets extend `print/base_print.html`.
Guarded by `certificates/tests.py`.

### Blood bank

The `bloodbank` app (`/bloodbank/`, feature `bloodbank`, its own module; roles
ADMIN/LABTECH/DOCTOR/NURSE). `BloodUnit` is one physical bag with a status machine
(AVAILABLE → RESERVED/ISSUED, or EXPIRED/DISCARDED); `is_available` is **derived**
(`status == 'AVAILABLE' and not is_expired`, `is_expired` from `expiry_date`), so an
out-of-date bag is never offered even if nobody re-flagged it. `BloodDonor` is the
donor register; `BloodIssue` issues a unit to a patient — done under
`select_for_update()` inside `transaction.atomic()`, re-checking `status ==
'AVAILABLE'` so two clerks can't issue the same bag, and stamping the unit ISSUED.
Nothing here bills; a transfusion charge, if any, goes through the normal
service-invoice path. The dashboard shows available counts per group from a single
grouped pass (no query per group). Guarded by `bloodbank/tests.py`.

### Vaccination / EPI

The `vaccination` app (`/vaccination/`, feature `vaccination`, its own module;
roles ADMIN/DOCTOR/NURSE). `Vaccine` is the schedule catalogue — a **global**
reference table (Pakistan's EPI schedule is a national standard, like
`DiagnosisCode`), seeded by `seed_epi` (idempotent, ~18 EPI vaccines) and
extendable by an admin on `/vaccination/catalogue/` (role-gated to ADMIN even
though DOCTOR/NURSE hold the feature). `VaccinationRecord` is the tenant-scoped
dose given to a patient; `next_due_date` is **stored** (the vaccinator sets it at
the visit) rather than derived, because the interval depends on the child's actual
visit. `vaccination_card` extends `print/base_print.html` (the immunization card);
`due_list` (`/vaccination/due/`) lists doses due/overdue. Guarded by
`vaccination/tests.py`.

### Consent forms

The `consent` app (`/consent/`, feature `consent` in the `opd` module; roles
ADMIN/DOCTOR/NURSE). `ConsentTemplate` is the reusable wording (surgery,
anaesthesia, blood, etc.), **per-tenant** so a hospital keeps its own approved
text; template management is role-gated to ADMIN (`/consent/templates/`).
`ConsentForm` is one signed instance — the body text is **copied onto the record**
at creation (a frozen copy), never referenced live, so editing a template later can
never rewrite what a patient already signed. The create form pre-fills the wording
from a chosen template client-side (a `json_script` map keyed by template id); the
print sheet (`print/base_print.html`) carries signature lines for
patient/guardian, witness and doctor. Guarded by `consent/tests.py`.

### Doctor availability

`Doctor.availability(at=None)` returns `{'available', 'state', 'label'}` from two layers,
in this order:

1. **`DoctorAvailabilityOverride`** for that date — one click from the OPD board. It wins.
2. **`DoctorSchedule`** rows — the weekly OPD timings (a doctor may have two per day).

Neither works alone: timings leave a doctor on leave showing as available, and a bare
manual switch has to be flipped every morning by someone who remembers. The override is
stored **per date**, never as a flag on the doctor — that is what stops today's leave from
hiding them tomorrow.

The visit screen shows only doctors who are sitting, with the rest behind a checkbox —
`VisitForm` still accepts any active doctor, because an emergency must be bookable against
someone who is off.

`availability()` reads `schedules` / `availability_overrides` via `.all()`, so **always
fetch doctors through `opd.availability.doctors_with_availability()`** — it prefetches both
(overrides filtered to today). A plain `Doctor.objects.filter(...)` on those screens is two
extra queries per doctor.

### Cross-module pipelines

These handoffs are the backbone of the app; each creates a record, notifies a role via `Notification.send_to_role(hospital, role, message, link)`, and pre-fills the receiving form:

- **Prescription → POS**: doctor writes an Rx (`status` `PENDING`); pharmacy opens POS with `?prescription_id=` to pre-load the cart. Selling all Rx medicines marks it `DISPENSED`, a subset marks it `PARTIAL`. Pending queues filter on `status__in=['PENDING', 'PARTIAL']`.
- **Doctor advises admission / surgery**: `AdmissionRequest` / `SurgeryRequest` (status `Pending`) → reception/OT queue → confirming with `?request_id=` creates the `Admission` / `SurgeryRecord` and closes the request.
- **Ward medication → stock + discharge bill**: logging a `MedicationLog` against a catalogue `Medicine` with `source='PHARMACY'` reduces stock FEFO (locked row, inside `transaction.atomic()`) and freezes `unit_price` at that moment. Discharge then bills bed charges **plus every such dose**. `MedicationLog.charge` is derived (`unit_price × quantity`), never stored, so a later catalogue price change cannot rewrite an old bill.

  Three cases record with **no stock movement and no charge**, and none of them may be blocked — the dose is already inside the patient, so refusing to save would leave the chart lying about what was given:
  - `medicine` empty — an off-catalogue drug. Do not make the field required.
  - `source='PATIENT'` — the patient's own supply, bought outside or brought from home. The ward is only administering it; the pharmacy never issued it.
  - `source='PHARMACY'` but stock is short — the dose is still recorded, `unit_price` stays 0, ` [not deducted — pharmacy stock short]` is appended to `notes`, and the nurse gets a warning to have the pharmacy reconcile. **Do not turn this back into a `form.add_error`.**

  The medicine search box on `ipd/medication_form.html` is backed by **this patient's prescribed drugs** (`_prescribed_medicines`, from `PrescriptionItem` via `prescription__appointment__patient`), not the whole catalogue — a nurse gives what was prescribed. The full catalogue is behind the `#use-full-catalogue` checkbox for orders written on paper or during a round. Stock levels are shown as information only, never as a blocker.

  Discharge stores the raised invoice on `Admission.discharge_invoice` and redirects to `ipd:discharge_summary` — the printable A4 sheet (stay, diagnosis, rounds, medications given, itemised bill). `Admission.days_stayed` is the inclusive calendar-day count both the bill and the summary use.
- **Lab / imaging → billing**: ordering a test or scan auto-creates a pending `Invoice` via `billing.services.create_service_invoice`.
- **Withdrawing an ordered service** (the patient refuses it) runs the pipeline backwards — see "Cancelling what the patient refused" below.
- **Reorder → purchase order**: `inventory.services.reorder_suggestions()` (sales velocity based) feeds `reorder_to_po`, which creates draft `PurchaseRequest`s grouped by supplier.

### Cancelling what the patient refused

A doctor orders three tests and the patient wants two. Every ordered service can
therefore be **withdrawn** — lab test, scan, prescribed medicine. Three rules hold
across all of them and are the whole design:

1. **Cancel, never delete.** Each model carries `is_cancelled`/`status='Cancelled'`
   plus `cancelled_at` / `cancelled_by` / `cancel_reason`, and **the reason is
   mandatory** (the services raise `ValidationError` on a blank one). "Why was this
   test never done" has to stay answerable, and the printed lab report shows the row
   as *Cancelled — not performed* rather than quietly listing two tests where three
   were asked for.
2. **Work already done cannot be withdrawn.** A `TestResult` with a `result_value`,
   or an `ImagingStudy` with findings, is refused — the lab used the reagent, the
   scan was performed. That is then a billing decision (void/refund), not a lab one.
   `cancel_order` refuses the *whole* order if any live test has a result, so a
   part-finished order can't be wiped in one click.
3. **The money follows, but cash already taken never vanishes.**
   `billing.services.cancel_invoice_charge(invoice, description)` drops the matching
   `InvoiceItem` and re-derives subtotal/discount/tax/total through
   `_rederive_totals` (same SiteSettings maths that built it), VOIDing the invoice
   when the last line goes. It **never lowers `paid`** — the excess comes back as
   `refund_due` for the desk to hand over in cash, because the day book has already
   counted that money. It matches the line **by description**, exactly the string
   the create path wrote (`f"Lab: {name}"`, `f"{modality}: {study_name}"`); a
   zero-price service never got a line, so `removed` is False and that is correct,
   not a failure. It locks via `Invoice._base_manager` on purpose — `objects` and
   `all_objects` are both `TenantManager`s and would re-scope a row the caller has
   already been authorised for (and miss a legacy `hospital=NULL` invoice).

Per module:

| | Service | Entry point | Bill effect |
|---|---|---|---|
| Lab | `lab.services.cancel_test` / `cancel_order` | `lab:test_cancel` / `lab:order_cancel` | line off the invoice; last one VOIDs it |
| Imaging | `imaging.services.cancel_study` | `imaging:study_cancel` | one study = one line, so normally VOID |
| Medicine | `prescriptions.services.cancel_item` / `cancel_prescription` | `rx_item_cancel` / `prescription_cancel` | **none** |

**Medicine is the odd one out and that is not an oversight**: a medicine is charged
when the pharmacy *dispenses* it at the POS, not when it is prescribed, so refusing
one means it is simply never sold — there is no invoice line to remove. What the
cancel fixes there is the *queue*: `_sync_status` flips an Rx whose every item is
declined to `CANCELLED`, so it stops sitting in the pharmacy's `PENDING` list for
ever. The POS reads `Prescription.active_items` (not `items`) both when pre-loading
the cart and when deciding DISPENSED vs PARTIAL — with `items` a declined line would
keep the Rx PARTIAL for ever. `ipd._prescribed_medicines` filters cancelled items out
too, so the ward is never offered a drug the patient refused.

**Who may cancel** — deliberately not the same list as who may order. The counter
staff who actually hear the patient say no are included; **reception is not**, since
they never have that conversation. `lab.views.CANCEL_ROLES` = ADMIN/DOCTOR/LABTECH,
`imaging.views.CANCEL_ROLES` = ADMIN/DOCTOR/SONOGRAPHER,
`prescriptions.views.CANCEL_ROLES` = ADMIN/DOCTOR/PHARMACIST. The views pass
`can_cancel` into the detail templates so a button never links into a 403.
`prescription_detail` and the two Rx cancel views are gated
`feature_required('prescriptions', 'pos')` — **the pharmacist holds `pos`, not
`prescriptions`**, and without the second key the one person standing in front of the
patient cannot do this. It shows them nothing new; the POS already pre-loads the Rx.

The person who ordered it is **notified directly** (`Notification.objects.create` to
`ordered_by` / `referred_by` / the prescribing doctor) — not `notify_admins`, which
is reserved for owner-level exceptions. A doctor who is never told waits for a result
that is not coming.

Cancelled rows leave the working queues: `lab.order_list` gained a `?show=cancelled`
tab and its `completed` tab excludes `Cancelled` explicitly (a bare
`exclude(status='Pending')` filed a withdrawn order as finished work);
`imaging.study_list` excludes them by default with a `?show=cancelled` toggle; the
sidebar badges already keyed on `status='Pending'`. `collect_payment` refuses on a
cancelled order/study, and `order_results_edit` builds its formset from
`results.filter(is_cancelled=False)` so the lab cannot type a value into a refused
test and make it chargeable again. Guarded by `tests/test_cancellation.py`.

Not offline in v1: a cancel rewrites money and state the device cannot see (the same
reason deletes and invoice voids are excluded — see "Offline data entry").

### Inventory & dispensing

Stock lives in `StockBatch` rows; `Medicine.quantity` is an aggregate that can drift from `batch_quantity` (hence `reconcile_stock`). Use the derived properties rather than raw `quantity`:

- `sellable_quantity` — non-expired batches only; this is what `sales.services.create_sale` checks and what `is_low_stock` uses.
- `reduce_stock` dispenses **FEFO over non-expired batches only**, so an expired batch can never be sold.
- `return_sale` quarantines expired returns (on hand but not sellable).
- `SaleItem.cost_price` freezes the batch COGS at sale time; the profit report depends on it.

`inventory/safety.py::screen_medicines()` produces allergy and duplicate-salt warnings (substring matching — advisory only, not a real drug-interaction database). It is wired into both `prescription_create` and the POS.

### Patient numbering (MRN)

`Patient.mrn` is **unique within a hospital, not globally** — every tenant numbers its own
patients from 1, so `SGH-000001` at one hospital and `GUL-000001` at another are both
number 1 and must not collide. Two `UniqueConstraint`s enforce it; the second one exists
because SQL treats `NULL` hospitals as distinct, which would otherwise leave a single-site
install unconstrained.

`patients/services.py` allocates: `next_mrn(hospital)` locks that hospital's
`SiteSettings` row, bumps `mrn_last_number` and formats `PREFIX-000001`. The counter lives
on `SiteSettings` because that row is already the per-hospital singleton **with a
hospital-less fallback**, so one lock serves both a SaaS tenant and the desktop build.
The prefix comes from `SiteSettings.mrn_prefix`, or is derived from the brand name
(`derive_prefix`: initials for a multi-word name, leading letters for one word).

Allocation happens in `Patient.save()`, not the form, so `seed_demo`, imports and fixtures
all produce numbered patients. **`saas.signals.auto_assign_hospital` is a `pre_save`
receiver and therefore fires inside `super().save()` — too late.** `Patient.save()` resolves
the hospital itself before allocating; remove that and every web registration is numbered
off the global counter instead of the tenant's.

The MRN box in `PatientForm` is `disabled` and `clean_mrn` returns the instance's existing
number (blank on create), so a posted value is ignored rather than trusted — the field is
displayed, never entered. On create the shown number is only a *preview*: the real one is
reserved in `Patient.save()`, because two receptionists with the form open would otherwise
both be holding the same one. An MRN passed in code (seeds, imports) is still kept as-is
and does not consume a sequence number. Changing the prefix never rewrites MRNs already
issued.

**CNIC** is stored in exactly one shape — `XXXXX-XXXXXXX-X`. `PatientForm.clean_cnic`
strips everything non-numeric, requires 13 digits and re-inserts the dashes, so an import
or a JS-off browser lands in the same format the reception search expects. The dashes
appear as the user types (`templates/patients/patient_form.html`).

**Age vs date of birth.** Reception may know either one, so `templates/patients/patient_form.html`
fills each from the other in the browser. The rule they follow is not symmetric:

- `dob` is fact. `Patient.save()` always recomputes `age_years` from it, so a typed age
  that disagrees is overwritten.
- **Years alone** does not produce a stored `dob`. The form suggests one (visibly marked
  approximate, only into an empty field) for the user to accept or correct; the server
  never writes it, because a made-up day reads as fact on a medical record.
- **Years + months/days** does. `PatientForm` carries form-only `age_months` / `age_days`
  and `clean()` folds them into `dob` — that entry is day-precise, so deriving the date is
  arithmetic on what reception said, not a guess. They are form-only on purpose: storing
  three numbers next to `dob` would give two answers to the same question.

Display age with **`patient.age_display`** — `'34y 5m 12d'`, `'7m 3d'`, `'4d'`, `'Newborn'`,
zero parts dropped. Years alone is useless in paediatrics, and `age_years` is only true on
the day it was typed (a patient registered at 30 otherwise still reads 30 five years on).
`current_age` remains the whole-year integer for logic and `{% if %}`.

Patient fields are laid out once in `templates/patients/_fields.html` and included by both
the registration page and the reception visit screen — the CNIC/age script lives there, so
rendering the form with `as_p` anywhere else silently loses it.

`Patient.age_parts_on()` counts whole months first, then measures leftover days from that
month-anniversary. Do not "simplify" it into subtracting the calendar fields and borrowing
a fixed number of days — 31 Jan to 1 Mar borrows 28 from February and yields a negative day
count. `templates/patients/patient_form.html` mirrors the same algorithm in JS; the two
must agree.

### Branding & print

`user_mgmt.SiteSettings` is a per-hospital singleton (`OneToOneField` to `Hospital`, nullable) holding brand name, logo, colours, receipt header/footer, print theme, enabled modules, and `show_doctor_to_pharmacy`. `SiteSettings.load()` resolves it from `get_current_hospital()`, creating the row on first access; with no hospital it reuses the single hospital-less row.

**Never call `SiteSettings.load()` from a management command to brand a specific tenant.** It resolves through the thread-local hospital, which no command binds, so it falls through to the hospital-less **platform** row — `seed_public_demo` renamed the whole site "Sehatyar Demo" this way while leaving the demo tenant with no settings of its own. Address the row explicitly: `SiteSettings.objects.get_or_create(hospital=<tenant>)`.

**Every `branding.logo_image` `<img>` carries an `onerror` fallback** to `static/img/sehatyar-logo.png` (`base.html`, `registration/login.html`, `print/base_print.html`). A row can name an upload whose file is no longer on disk — a DB restored without its media, a wiped upload — and Django's `{% if branding.logo_image %}` only checks the field is set, not that the file exists, so the sidebar rendered a broken-image icon on every screen. Verifying server-side would be a filesystem stat on every render; the browser already knows. Guarded by `tests/test_seo.py::BrokenLogoFallbackTest`.

The default logo `static/img/sehatyar-logo.png` must keep its **transparent** corners. It originally shipped with an opaque near-white (246,246,246) background baked in, which showed as white corners everywhere it sat on a coloured surface — the dark sidebar most visibly. If the file is ever regenerated, key the outer background out (flood-fill from the corners, not from every border pixel — the ECG line is white too and exits at the right edge). `pwa_views.icon` composites it onto white for the home-screen icon, which is correct: a transparent app icon renders black or white on Android anyway.

The settings screen can **theme the app from the logo**: ticking "Pick the theme colour from the logo" on save runs `user_mgmt.color_utils.dominant_color()` (Pillow — picks the most common vivid, non-white/black, non-transparent pixel) over the uploaded/current logo and sets `primary_color` + a `darker()` accent. It only fires when the box is ticked, so a hand-set colour is never overwritten silently.

`default_theme` (light/dark/auto) sets the tenant's default appearance: `base.html` applies it only when the *device* has no `localStorage.theme` of its own, so a per-device toggle always wins; `auto` follows `prefers-color-scheme`. `whatsapp_enabled` shows a **Send on WhatsApp** button on the patient bill — a free `wa.me` link (no gateway/credentials), with the number normalised by the `wa_number` filter (`billing/templatetags/wa.py`: `03…`/`+92…`/`0092…` → `92…`) and the bill summary URL-encoded into the message. `show_bill_qr` prints a scannable QR of the bill summary (plain text — charged/paid/outstanding, so a phone camera reads it with no internet) on the **printed** bill; the `qr_data_uri` tag (`billing/templatetags/qr.py`) renders it inline as a data-URI PNG. `qrcode` is an **optional** dependency (in `requirements.txt` and `requirements-build.txt`) — the tag returns `''` if it is missing, so a bill never fails to print; the `patient_bill_print` view builds `qr_text` only when the toggle is on.

**Invoice numbering** mirrors the MRN counter exactly. `Invoice.number` (e.g. `INV-2026-00001`) is allocated per hospital by `billing.services.next_invoice_number()`, which locks the same `SiteSettings` row (`invoice_prefix` / `invoice_last_number` / `invoice_year_in_number` / `invoice_number_year`) — with the year on, the count restarts each January. Allocation is in **`Invoice.save()`** (resolving the hospital itself, because `saas.signals.auto_assign_hospital` fires inside `super().save()` — too late, same trap as `Patient.save()`), so every creation path — service/OPD invoices, discharge bills, offline replay, seeds — gets a numbered invoice. Rows created before this feature have `number = NULL`; **display them with `invoice.display_no`**, which falls back to `#id`. A partial `UniqueConstraint` guards `(hospital, number)` for non-null numbers. Pharmacy POS receipts keep their own `Sale #id`.

**Currency symbol** is per-hospital (`SiteSettings.currency_symbol`, default `Rs`). In templates use **`{{ currency }}`** — the `site_branding` context processor injects it on every render (falling back to `Rs` when branding can't load), so write `{{ currency }} {{ amount }}`, never a hardcoded `Rs`. In Python (flash messages, the printed-QR text) use **`user_mgmt.models.current_currency()`** — it runs a query, so call it once per action, never in a loop or hot path. Model `__str__`s that embed a rate (e.g. `Bed`, `ProcedureType`) deliberately keep the literal `Rs` to avoid an N+1 in dropdowns, and the SaaS-owner portal keeps `Rs` as the platform currency (separate from a tenant's).

**Bill maths (tax / standing discount / rounding)** live on `SiteSettings` (`default_tax_percent`, `default_discount_percent`, `bill_rounding` with helpers `tax_on(base)` and `round_total(amount)`) and **all default to a no-op** (0% / no rounding), so behaviour is byte-identical until an admin opts in. They are applied in exactly two places: `sales.services.create_sale` (POS/wholesale/offline) and the `billing.services` invoice builders (`create_service_invoice` — which also covers lab, imaging and IPD discharge — and `create_opd_invoice`). Order of operations: subtotal → **minus discount** → **plus tax** (`tax_on` of the discounted base) → **round**. `Sale.tax` / `Invoice.tax` store the tax amount. The **standing discount %** applies to `create_sale` only, and only when the caller passes `discount=None` — the POS sends `None` when its discount box is left blank and an explicit number (including `0`) otherwise, so it never stacks on a counter discount; auto-invoices don't get it. The POS summary JS (`templates/sales/sale_create.html`, via `window.BILL_TAX_PCT` / `BILL_DISC_PCT` / `BILL_ROUND`) mirrors the same maths so the on-screen grand total matches what the server stores. `SiteSettingsForm.OPTIONAL_DEFAULTS` lets these (and the currency/invoice fields) fall back to their default when a box is blank, so a partial settings post never 500s.

Two things here are load-bearing:

- The template context key is **`branding`**, not `site` — Django's `LoginView` injects its own `site` variable which would shadow it.
- **Never force `pk=1`** when saving. Inserting an explicit primary key on PostgreSQL desyncs the id sequence and the next tenant's row collides with `duplicate key ... (id)=(1)`. Migration `user_mgmt/0008` resyncs the sequence.

### Panel / Insurance / Sehat Card billing

The `panels` app (`/panels/`, feature `panel`, bundled in the `finance` module;
roles ADMIN/ACCOUNTANT/RECEPTIONIST) handles institutional payers — private
insurance, corporate panels, and the govt **Sehat Card** (Sehat Sahulat). A
`Panel` is a per-tenant ledger the same shape as a `customers` khata: invoices
billed to it are debits it owes, `PanelPayment`s are credits.

- **A patient is linked to a panel** via `Patient.panel` (+ `panel_member_id`, the
  card/policy number), set at registration/reception. From then on **every invoice
  path auto-attributes**: `billing.services.create_service_invoice` and
  `create_opd_invoice` default `invoice.panel = patient.panel` and stamp
  `claim_status='PENDING'`. So lab, imaging, IPD-discharge and OPD bills for a
  covered patient all become claims with no extra step. For OPD specifically, a
  covered patient's consultation is left **unpaid** (`paid=0`, owed by the panel)
  where an uncovered patient still pays upfront (`paid=total`).
- **Pharmacy POS sells to a panel too** (`Sale.panel` FK). Unlike the invoice
  paths, the POS does **not** auto-attribute — the cashier ticks *Bill to panel*
  (shown only when the linked patient has one; the view maps `patient_id → panel`
  in `panel_patients`). `sales.services.create_sale(panel=...)` then defaults the
  sale to unpaid (panel owes it, any co-pay via the `paid` box) and, crucially,
  **routes the unpaid balance to the panel instead of a customer khata** — the
  `credit_amount > 0` branch requires a customer only when `panel is None`.
- **What the panel owes is `invoice.balance`** (`total − paid`); `paid` is the
  co-pay collected from the patient at the counter. `Invoice` gained `panel`
  (PROTECT), `claim_status` (PENDING/SUBMITTED/APPROVED/PAID/REJECTED),
  `claim_number` and `panel_settled`.
- **Covered services (per panel).** `Panel.covered_services` (JSONField list of
  `Panel.SERVICE_KEYS` — OPD / PHARMACY / LAB / IMAGING / IPD / PROCEDURE) says
  which service categories the card pays for, so one card can be "OPD only",
  another the inpatient package (IPD + lab + imaging, no pharmacy), another the
  whole hospital. **Empty list = no restriction = covers everything** — that
  default keeps every pre-existing panel (and every full-cover card) working with
  no data migration. `Panel.covers(service)` is the gate (empty→True, `service`
  None→True). Every billing path **tags its bill with a `service`**:
  `create_opd_invoice`→`'OPD'`, `create_sale`→`'PHARMACY'`, and
  `create_service_invoice(..., service=)` passed by its callers (lab→`LAB`,
  imaging→`IMAGING`, ipd discharge→`IPD`, ot/maternity→`PROCEDURE`,
  emergency→`OPD`). A bill for a service the card does not cover is billed to the
  **patient** as normal (no claim). The panel form renders `covered_services` as
  checkboxes; the panel list shows a "Covers" badge.
- **Coverage limits (per patient).** `Patient.panel_coverage_limit` (0 = unlimited)
  caps what the panel covers for that patient — e.g. a Sehat Card annual limit.
  `panels.services.apply_coverage(patient, total, panel, service=None)` is called
  from all three billing paths and returns `(effective_panel, floor)`: it first
  drops the panel when `not panel.covers(service)`, then the panel owes at most the
  patient's **remaining** coverage (`coverage_used` = the panel-owed portion of
  their prior panel bills — payments do *not* restore it), any excess is a `floor`
  the patient must pay, and an **exhausted** limit drops the panel entirely so the
  patient is billed normally. `floor` never lowers a co-pay the caller already set
  (`paid = max(paid, floor)`).
- **Outstanding is computed, never stored** (`panels.services.outstanding_for` /
  `outstanding_map`) — billed − co-pay − panel payments, over **both** service
  invoices and non-returned pharmacy sales — so it cannot drift the way the stored
  `Customer.balance` can. `outstanding_map` uses grouped aggregates keyed by
  `panel_id` (no query per panel in the list loop).
- **Per-claim payment allocation.** `record_payment` FIFO-allocates each panel
  payment across the panel's open claims oldest-first (`allocate_payment` →
  `_open_claims`, tie-broken by pk), filling each claim's `panel_settled` and
  flipping a fully-settled invoice's `claim_status` to `PAID`. A `linked_invoice`
  on the payment is settled first. The panel ledger (`/panels/<pk>/ledger/`) lists
  invoice claims, pharmacy sales and payments with a running balance, per-claim
  settled amount, and an inline claim status/number update (`panel_claim_update`).
- Not offline in v1 (panels are config + receivables; add kinds later if needed).
  Coverage is enforced two ways — a **covered-services list** (above) and a **money
  limit** — but **not** as package rates: a fixed price per covered procedure per
  scheme is still out (a covered service is billed at the hospital's own rate, then
  gated/capped). Guarded by `panels/tests.py`.

### Staff HR (attendance, leave, payroll)

The `hr` app (`/hr/`, feature `hr`, its own module; roles ADMIN/ACCOUNTANT) is the
people side. `StaffProfile` (OneToOne to `User`, optional — kept off the auth model)
holds designation, `monthly_salary`, joining date. `Attendance` is one row per
`(user, date)` (unique) marked from a daily grid that **upserts** — re-saving a day
moves the mark, never duplicates it — with a monthly present/absent/leave summary.
`LeaveRequest` (approve/reject stamps `decided_by`). `SalaryPayment` is a payslip
whose `net` is a derived property (`basic + allowances − deductions`, never stored)
printed via `print/base_print.html`; `salary_create` pre-fills `basic` from the
staff profile when opened with `?user_id=`. Staff are the hospital's own users
(`User.objects.filter(hospital=...)`, superuser sees all). Guarded by `hr/tests.py`.

### Install-as-app (PWA)

The site installs as an app (phone home screen, desktop) carrying **each tenant's own**
name, logo and colour — not "PharmaDost". `user_mgmt/pwa_views.py` serves a per-tenant
`manifest.webmanifest`, a Pillow-rendered `app-icon-<size>.png` (the uploaded logo, else the
default **`static/img/sehatyar-logo.png`** — the Sehatyar heart-and-pulse logo the templates
also show; a drawn heart+pulse mark is the last-resort fallback if that file is missing), a service worker at **`/sw.js`** (root, so its scope is
the whole site), and an `/offline/` fallback. All read `SiteSettings.load()`, so branding
follows the logged-in tenant. The four browser-fetched endpoints are in
`LoginRequiredMiddleware.ALLOWED_NAMES` — if they redirect to login, install breaks.

**A browser does not send cookies for either of them.** The manifest link therefore carries
`crossorigin="use-credentials"`, and the icon URL carries `?t=<brand_token>` naming the
tenant's `SiteSettings` row. Without both, `SiteSettings.load()` sees an anonymous request,
falls back to the hospital-less row, and every install lands on the home screen branded
"Sehatyar" with the default letter icon instead of the hospital's own name and logo — which
is exactly what shipped. `brand_token` is signed (so the endpoint cannot be walked for every
tenant's logo) and deterministic bar `updated_at` (stable URL, but a replaced logo appears).
Renaming an *already installed* app is up to the browser; on Android the app has to be
removed from the home screen and re-added.

The service worker is **shell-cache + offline-read**: it keeps the app opening and shows
already-visited pages on a dropped connection, with a clear offline page. Offline *writes*
are handled separately by the outbox below, not by the service worker.

Five things about it are load-bearing:

- **`install` waits on `_CRITICAL_URL_NAMES` only** — the offline page, the dashboard and
  the two static assets — and the other ~45 screens are fetched afterwards, from a `warm`
  message the page posts once the worker is active. Blocking activation on the whole shell
  meant that on a clinic connection **no worker existed** for as long as the download took,
  and staff who turned the wifi off in that window got the browser's own "you cannot reach
  this site" page on every tap. Nothing slow may go in `activate` either: fetch events are
  held until it settles.
- **Every branch of `fetch` must end in a `Response`.** `respondWith(undefined)` is a
  network error and the browser answers it with that same error page, so the last-resort
  offline HTML is *built into* the worker rather than fetched from the cache — a fallback
  that has to be retrieved is unavailable in precisely the case it exists for.
- **The shell is built from url *names*** (`pwa_views.SHELL_URL_NAMES`, reversed per
  request), not hand-written paths. Hard-coded paths rot silently: `/opd/visit/` and
  `/lab/order/new/` sat in that list resolving to nothing, so the two screens the offline
  outbox most needed were never cached. `user_mgmt/tests_pwa.py` asserts every shell url
  resolves and that each offline-capable screen is in it. **Every new offline form's page
  belongs here** — a form that will not open cannot be filled in.
- **The cache name includes the signed-in user**, not just the tenant. The worker caches
  whole rendered pages, and those hold patient names and bills; on a shared desk where a
  doctor signs out and a receptionist signs in, one cache would serve the doctor's pages to
  whoever is there now. A different user → a different cache name → `activate` deletes the
  old one.
- **Redirects and auth paths are never cached** — in the fetch handler (`res.redirected` is
  skipped, `NO_CACHE` covers `/accounts/`, `/login/`, `/logout/`, `/admin/` and the sync
  endpoints) **and in the pre-cache**. The pre-cache is the easy one to get wrong:
  `cache.add()` follows redirects and stores the final response under the URL that was
  *asked for*, so warming while signed out filed the sign-in page under `/sales/new/`,
  `/patients/` and every other screen — and each of them then showed a login form offline,
  which from the ward is indistinguishable from having been logged out. Use the worker's
  `save()` helper (fetch, then `res.ok && !res.redirected`), never `cache.add`. Warming is
  skipped outright when `SIGNED_IN` is false.

Static assets are matched exactly first and then with `ignoreSearch`, because templates link
them as `app.css?v=1.7`: after a version bump the exact URL misses, and without the second
lookup the page renders offline with no stylesheet at all.

**Offline search across a whole registry (not one cached page).** List screens are
paginated, so the service worker caches only *one* page — an offline search filtering the
DOM (`static/js/offline.js::filterDomTables`) would see just those ~25 rows. So a page may
declare `window.__offlineIndexConfig` (see `templates/patients/patient_list.html`): a
compact JSON index URL, a per-user `storeKey`, the searchable `fields`, and the table
`columns`. `offline.js::primeOfflineIndex` fetches that index while online (throttled
~3 min, other users' saved indexes dropped to mirror the per-user SW cache) and stores it in
`localStorage`; when the search is run offline, `offlineIndexSearch` renders matches from the
saved index into the table, so **every** saved patient is findable, not just the cached page.
The only wired-up index today is the patient list — its endpoint is
`patients.views.patient_index` (`/patients/index.json`, feature `patients`), scoped through
`_visible_patients` exactly like `patient_list` (tenant + role narrowing), capped at 5000
rows. It is **not** in `SHELL_URL_NAMES` (it is data, refreshed live, not a shell page).
Guarded by `tests/test_security.py` (`patient_index` tenant-scoped + fail-closed for a
hospital-less user). Column cell HTML in the config is template-authored constant; all
patient values are escaped in `buildIndexRow`.

**A service worker only runs on a secure origin**, so `SECURE_SSL_REDIRECT` is on whenever
`USE_SSL` is (`DJANGO_SSL_REDIRECT=false` disables it). Plain http was a dead end in two
ways at once — no worker, and `SESSION_COOKIE_SECURE` meant a sign-in never stuck — with
nothing on screen saying so. `/get-app/` now reports what is actually true on *this* device:
secure origin, worker running, N of M screens saved, and a button to save them. A silent
failure here previously had nowhere to be seen.

### Offline data entry (outbox + sync)

Every data-entry screen in the app works with **no connection**: the entry is queued on
the device and replayed to the server when it can be reached. Reading offline is the
service worker's job (previous section); writing offline is this.

- **Client** (`static/js/offline.js`, loaded on every page from `base.html`): any
  `<form data-offline-kind="...">` is intercepted. Before each submit the client asks the
  **server** whether it is reachable (`GET /offline/ping/`, 204, result cached ~4s). Reachable
  → `form.submit()`, i.e. the native submit, unchanged. Not reachable → the form is
  serialised, stamped with a `crypto.randomUUID()` and stored in an IndexedDB `outbox`.
  The queue is POSTed to `/offline/sync/` on the `online` event, on load, on tab focus and
  on a 60s timer, in slices of `MAX_BATCH`.

  **Do not go back to gating on `navigator.onLine`.** That flag only reports a network
  *link*; on a clinic router with a dead uplink it stays `true`, the form submits into the
  void and the browser replaces the page with an error — the typed entry is gone. The probe
  is the whole point. A session that has expired answers with a redirect, which counts as
  unreachable, so the entry is queued instead of being thrown away on a login screen.

  Two more client rules: the interceptor **returns if `e.defaultPrevented`** (a page's own
  validation already blocked the submit — queueing would smuggle past it), and `csrf()`
  reads the **cookie first**, because a service-worker-cached page carries a stale `<meta>`
  token and Django rotates the token on login. File inputs cannot be queued; they are
  dropped with a toast naming them, and re-attached once online.
- **Server** (`offline_sync/`): `sync` is called **by the logged-in browser**, same-origin
  with the session cookie — *not* a headless job — so `request.user`, hospital scope and the
  thread-local are all live. Idempotency is by `ClientAction.client_uuid` (unique), and the
  ledger row is **created first, inside the same `transaction.atomic()` as the handler**:
  the unique index is what serialises two concurrent replays, and a rollback takes the claim
  with it. Do not move the ledger insert back outside the transaction — a crash between the
  commit and the bookkeeping would let a retry create a second patient or sale.
  `ValidationError` / `PermissionDenied` / `Http404` are filed `FAILED` (permanent, shown on
  the outbox screen); any other exception records nothing so the client retries.
- **Handlers reuse the live forms/services** (`offline_sync/handlers.py`): a queued visit is
  bound to the same `PatientForm` + `VisitForm` and booked through `opd.views._book_visit`;
  a queued dose goes through `ipd.services.log_medication`; a queued lab order through
  `lab.services.create_test_order`. There is no looser second path. The service modules
  (`lab/`, `imaging/`, `ipd/`, `ot/`, `prescriptions/services.py`, `opd.services.bill_and_notify`)
  exist for exactly this: the online view and the replay call one function.
- **The outbox screen** is `/offline/queue/` (sidebar → App → Offline Queue, and the
  floating badge links to it). It lists what is waiting and what was **rejected, with the
  reason**, and offers Try again / Discard. A rejection that is only a six-second toast is a
  lost record.
- **The offline handoff is paper, and that is not a workaround** (`/offline/slip/`).
  Two devices with no server between them cannot notify each other — there is no channel,
  and no amount of code creates one. So a visit registered offline redirects to a
  **provisional slip** the patient carries to the doctor's room, exactly as clinics worked
  before computers. It renders entirely from the browser's outbox (the entry has not synced
  yet), so `offline/provisional_slip.html` pulls in `js/offline.js` itself — the print base
  does not extend `partials/base.html`. Its `OFF-3` reference is a **local counter, marked
  provisional**: MRN and token are still server-issued at sync, and must never be guessed
  client-side. `visit_form.html` carries `patient_label` / `mrn_label` as hidden fields
  purely so the slip can name a returning patient (an id on paper tells the doctor nothing);
  the server ignores them. Anyone who wants the handoff to appear on the *doctor's screen*
  offline needs the LAN server — say so rather than attempting a peer-to-peer scheme.
- **A synced queue must not ring like new work** (`accounts/replay.py`). Notifications are
  raised when the queue is *replayed*, not when the work happened, so without this a desk
  that worked through a four-hour outage pings the doctor ten times about patients they
  already saw and tells reception to allot a bed that is already occupied — finished work
  reading as pending work. `sync` therefore wraps the batch in `replaying()`, and
  `Notification.save()` (the one chokepoint every creation path goes through) stamps each
  message with the time it was actually entered on the device — `⏱ 10:32 offline —` — and
  files it **read**. One unread summary per affected person follows: "📥 14 entries made
  offline (10:05–13:40) synced just now." Nothing is deleted; the detail is all still there.
  The exception is work that is *still outstanding* — a short-stock sale the pharmacist must
  go and count — which passes `send_to_role(..., force=True)` and stays unread. The client
  sends each action's `at` (its IndexedDB `createdAt`) to make the stamp truthful.

Three things are load-bearing and must stay true:

1. **Server-authoritative numbers are assigned at sync, never by the client.** MRN, token
   and sale/invoice numbers come from the locked counters when the action is applied — the
   offline slip is provisional until then. Never let the client pick these.
2. **Stock cannot be made safe offline.** Two devices can each sell the last unit while
   offline; the physical overselling is real and no sync can undo it. An offline **sale**
   (`kind='sale'`) is replayed through `create_sale(..., on_short="record")`: it dispenses
   the in-date stock that exists, **bills the full quantity anyway** (the patient already
   has it), records the shortfall as a batch-less `SaleItem` (no COGS), and sets
   `Sale.needs_reconcile` + `reconcile_note`, notifying the pharmacist. The default
   `on_short="raise"` — every **live** POS sale — is unchanged and still refuses a short
   sale. Do not route a live sale through `"record"`, and do not "fix" the offline case by
   having the client reserve stock. The same shape applies to a ward dose
   (`ipd.services.log_medication` records it and flags the shortfall) and to an offline
   admission (the bed is re-checked under a lock at replay, and a clash is rejected
   permanently rather than double-booked).
3. **A form is offline-capable only when everything its handler needs is inside the form.**
   Where the online view takes the parent from the URL — the appointment an Rx hangs off,
   the patient of a clinical record, the admission of a dose — the template must carry it as
   a hidden `<input name="..._id">`. Without it the handler raised a NOT NULL error, which
   is not a `ValidationError`, so it was retried for ever behind a badge reading "waiting to
   sync". `handlers._parent()` now turns a missing or dangling id into a permanent rejection;
   keep new handlers using it.

**Coverage: all 48 kinds in `HANDLERS`** — visit, patient, patient_record, appointment,
department, doctor, payout, prescription, rx_preset, sale, medicine, adjustment,
purchase_return, supplier, supplier_payment, customer, customer_payment, lab, lab_result,
lab_test, imaging, imaging_report, scan_type, ward, bed, admission, admission_advise, round,
medication, vital, fluid, nursing_note, care_task, handover, discharge, surgery,
surgery_advise, surgery_category, procedure, expense, cash_closing, and the clinical
add-ons **antenatal_visit, vaccination, diagnosis, referral, consent, birth_certificate,
death_certificate** (bedside/front-desk forms, no billing or stock, so replay is a straight
form re-run; only `antenatal_visit` has a URL parent — `pregnancy_id` as a hidden input).
Still deliberately **not** offline: **blood-unit issue** (two devices could issue the same
physical bag — the stock-oversell problem, and it is a connected desk anyway), **HR**
(attendance is a bespoke grid not a ModelForm, salary is a cash payout done at a desk), and
the **delivery** record (billing + parallel baby-row arrays; the antenatal visit already
covers the bedside maternity case).

`offline_sync/tests_coverage.py::EveryKindAppliesTest` **walks `HANDLERS`**, so adding a kind
without adding a payload there fails the suite — that is deliberate, because a broken handler
is otherwise indistinguishable from a slow sync.

Deliberately **not** offline, and each for a reason: sign-in and user management (auth must
be verified by the server), site settings and the first-run wizard (they change what every
other screen renders), deletes and invoice voids (irreversible against state the device
cannot see), bulk price-list updates (they rewrite rows the device may hold a stale copy of),
and the two-step wholesale-order / purchase-order builders (they create an empty shell and
then add items, so queueing step one alone is worthless — the POS `sale` kind covers
pharmacy selling offline). Fully-offline sites with no internet at all are still better
served by the **desktop build in LAN mode** (next section), which has no sync step.

### The desktop build as a clinic LAN server

The outbox above saves *one device* through an outage. It does not run a hospital: each
device holds its own queue, so reception's ten registrations are invisible to the doctor
until the internet comes back, and **no notification is delivered at all** while the
server is unreachable. For a clinic where the internet is down most of the day (the KPK
case this was built for), the answer is to put the server in the room.

`desktop/launcher.py` therefore binds **`0.0.0.0`, not localhost** (`PHARMADOST_LAN=0`
opts out), so every phone and PC on the same wifi uses that one machine. From the app's
point of view it *is* online — notifications, tokens, live stock, the doctor↔reception
handoff all work normally, with no internet anywhere.

Four things there are load-bearing:

- **The detected LAN addresses go into `DJANGO_ALLOWED_HOSTS` *and* `DJANGO_CSRF_TRUSTED`**
  (both env vars `settings.py` already reads). Miss the first and every phone gets
  "Bad Request (400)"; miss the second and every form fails CSRF. `lan_addresses()` finds
  them with a UDP `connect()` that sends no packets and needs no internet — it only reads
  the routing table — plus `gethostbyname_ex` for a second interface.
- **The port is fixed** (8000, then 8080/8800/5000, `PHARMADOST_PORT` to force it). Staff
  bookmark `http://<ip>:8000` on a phone; a random port each launch breaks that daily.
- **Windows Firewall silently drops incoming connections**, so the launcher adds an
  inbound rule via `netsh` — which needs administrator. When it cannot, it prints exactly
  that, because otherwise the phones simply time out with nothing anywhere to explain why.
- **`DJANGO_SSL=false`**: on plain http over the LAN, secure-only cookies stop login
  working. The corollary is that the **service worker will not register on
  `http://192.168.x.x`** (browsers require a secure origin), so LAN phones get no page
  cache and no "install as app" — they do not need either, the server is on the wifi. The
  outbox still works there, because IndexedDB has no such restriction.

In-app, `/get-app/connect/` (sidebar → App → Connect a Device, shown only when
`PHARMADOST_LAN_URL` is set) prints the address and a QR. `qrcode` is an **optional**
import — without it the page still shows the address in large type, so the desktop build
must never hard-depend on it. Setup and troubleshooting for the clinic: `docs/lan_setup.md`.

**The LAN server and the hosted site are two separate databases** with no sync between
them. Do not build a feature that assumes otherwise without first building real two-way
sync (per-row versioning and conflict resolution) — a much larger piece of work.

**Offline subscription licence (desktop/LAN only).** The hosted site gates tenants by
`Hospital.expiry_date` server-side; the desktop build has no server to check against, so
it enforces its monthly subscription **on the device** with a signed licence key. The
launcher sets `PHARMADOST_DESKTOP=1` → `settings.DESKTOP_BUILD` → `user_mgmt.middleware.
DesktopLicenseMiddleware` runs (a **no-op on the hosted site**, so it is harmless in the
`MIDDLEWARE` list everywhere). It reads `user_mgmt.licensing.license_state(DATA_DIR)` every
request: within licence/trial the app runs (the middleware stamps `request.license_state`,
which `base.html` renders as a banner in the last `WARN_DAYS`); once expired or the trial
is over it returns the full-screen `desktop/license_locked.html` (HTTP 402) for every path
except static/media/`/accounts/`, login/logout and the licence page itself — so **every
phone on the LAN locks too**, since they all go through this one server. An admin unlocks
at Settings → Licence (`user_mgmt:license`, sidebar shown only on the desktop build).

The crypto is **asymmetric and pure-stdlib** (RSA via built-in `pow`, no bundled crypto
dep). `user_mgmt/licensing.py` carries only the **public** key and can *verify* a key but
never *mint* one — so the public repo leaks nothing that lets a clinic forge or extend its
own licence. The **private** key lives in `licensing/private_key.json` (**git-ignored — never
commit it**); the owner tools `licensing/keygen.py` (run once) and `licensing/sign_license.py`
(run per clinic per period) are the only things that sign. A fresh install gets a `TRIAL_DAYS`
trial; `license_state` also blunts clock-rollback via a stored `last_seen` (`today =
max(today, last_seen)`). A key **may be locked to one computer**: if the owner issues it with
a `machine` field (the clinic's `licensing.machine_id()` — Windows MachineGuid hashed, shown
to them at Settings → Licence), `license_state` returns `wrong_machine`/`ok=False` on any
other PC, so a copied install or a shared key will not run. Blank `machine` = runs anywhere
(default, backward compatible). Data restore is unaffected — only the key is machine-bound,
so moving PCs means re-issuing a key, not losing data. Per-tenant keys are generated one-click
from `saas.views.hospital_desktop_license` on the tenant page (clinic name + slug baked into
the token via `make_token(..., extra=)`), or ad-hoc at `saas.views.desktop_license`. The core lives **inside the `user_mgmt` app**, not a top-level
package, specifically so the PyInstaller build bundles it with every other app module (the
`.spec` is git-ignored and cannot be relied on to pick up a new top-level package). Guarded
by `user_mgmt/tests_licensing.py` (crypto, state machine, and the lock middleware — CI has
no private key, so the tests mint their own keypair, which *is* the security property).

**The desktop build backs itself up on every launch** (`desktop.launcher.backup_on_start`):
a zip of the DB + media into `DATA_DIR/backups` (last 14 kept), and — if
`PHARMADOST_BACKUP_DIR` is set (a USB stick / second drive / cloud-synced folder) — there
too. Only that off-machine copy survives the computer being stolen or its disk failing, so
the doc steers the clinic to set it. The in-app `backup_download` button remains for
on-demand copies.

**Cloud backup to the host + restore** (LAN install → hosted, *not* live sync). On every
launch `desktop.launcher.upload_backup_to_cloud` also POSTs the snapshot to the hosted site
(`PHARMADOST_CLOUD_URL`, default `https://sehatyar.online`) at `saas.views.backup_upload` —
best-effort in a daemon thread, a no-op with no licence or no internet — and **only when the
DB changed** since the last upload (sha256 of `db.sqlite3` vs `cloud_backup.json`), so an
idle clinic re-sends nothing. `backup_on_start` first `PRAGMA wal_checkpoint(TRUNCATE)`s so
the single-file snapshot is complete. **Auth is the install's signed licence token** (the
host verifies its signature with the public key; expiry is *not* required — a lapsed clinic's
data is still wanted), which also labels the backup by clinic name. The endpoint is
CSRF-exempt and in `LoginRequiredMiddleware.ALLOWED_NAMES` (it is called by the launcher, not
a browser). `saas.models.DesktopBackup` stores the zip (not tenant-scoped — external
installs, superuser-only), keeping only the **latest per install** (`KEEP_BACKUPS_PER_INSTALL
= 1`, so the host disk does not grow). The owner sees/downloads it at `/saas/backups/` and
hands it back after a loss. The clinic
**restores** at Settings → Restore (`user_mgmt.views.restore_upload`, desktop-only, ADMIN):
because a live SQLite file can't be swapped on Windows, the view only *stages* the uploaded
zip into `DATA_DIR/_restore_pending/` + a `RESTORE_PENDING` marker, and
`desktop.launcher.apply_pending_restore` does the swap on the next launch, **before Django
opens the DB**. Uploads are zip-slip guarded (`_safe_backup_members`) and must contain
`db.sqlite3`. This is **backup/restore, never merge** — the file is stored and handed back
as-is; true two-way LAN↔hosted sync remains the separate large project warned about above.
Guarded by `saas/tests_backup.py`.

Add a new kind by: writing a handler that reuses the online view's form/service, registering
it in `HANDLERS`, marking the form `data-offline-kind` **with any parent id as a hidden
input**, adding its page to `pwa_views.SHELL_URL_NAMES`, and adding a payload to
`tests_coverage.py`.

`base.html` injects tenant colours as CSS variables over `app.css`. When editing `static/css/app.css`, bump the `?v=X.X` cache-busting query string in every template that links it (`partials/base.html`, `registration/login.html`, `user_mgmt/setup.html`).

### Back, and pages that show pre-edit data

A cancelled test left the previous screen still showing the old amount until the
user pressed refresh. Nothing was wrong server-side — **the browser restored that
screen from its bfcache and never asked.** No amount of `Cache-Control` reliably
fixes bfcache; the page comes back from memory, request and all skipped.

So the server stamps a token and the page checks it:

- **`user_mgmt.middleware.DataVersionMiddleware`** issues a fresh `dv` cookie after
  every successful non-GET request. It is not secret and JS must read it, so it is
  deliberately **not** `httponly`. On a first visit the token is planted into
  `request.COOKIES` *before* the view runs, so the first page already agrees with
  the cookie the browser is about to receive and does not think itself stale.
- **`partials/base.html`** renders it as `<body data-dv="…">` — **only on GET**. A
  form re-rendered after a failed POST carries no stamp on purpose, or it could
  decide to re-fetch itself and throw the user's typing away.
- The script at the end of `base.html` compares the two on `pageshow` (only when
  `event.persisted` — an actual bfcache restore) and on `visibilitychange`, and
  calls `location.replace()` **only when they differ**. A Back with nothing written
  in between costs no request at all; that is the point of doing it this way rather
  than blanket `no-store`, which would re-fetch every Back on a clinic connection.

Three things there are load-bearing:

- **`location.replace()`, not `reload()`.** `reload()` re-submits a POST-rendered
  page; `assign()` piles up history entries until Back stops going anywhere.
- **The `sessionStorage['dvRetry']` guard.** Offline the service worker answers
  with the same cached copy, whose stamp is still old — without the guard the page
  re-fetches, gets the cache again, and spins for ever on a dropped link.
- **A rejected form still bumps the token, and that is deliberate.** Telling a
  successful POST-that-renders from a POST-that-failed needs a guess; guessing the
  other way brings the original bug back. Over-bumping costs one extra fetch on the
  next Back, under-bumping shows a bill that is no longer true.
  `_DV_SKIP_PREFIXES` exempts the notification endpoints, which change nothing any
  page displays.

**The topbar Back button (`#appBack`)** exists because the installed PWA has no
browser chrome — on a phone there is otherwise no way back at all. It is a real
`history.back()` (so the staleness check above applies), falling back to a
same-origin referrer and then `/dashboard/` when `history.length` is 1 — a page
opened from a bookmark, a link in another app, or the PWA start URL, where "back"
would leave the app. Alt+← is bound to it for the same reason.

**Bump `pwa_views._SW_REVISION` whenever `partials/base.html` changes.** The worker
caches whole rendered pages, so every saved screen keeps the old chrome until the
cache name changes.

Guarded by `tests/test_freshness.py`.

### Phones

Reception and the ward round both work off a phone, so a layout that only holds up
on a laptop is a broken layout. Three things carry that and are easy to undo:

- **Every table is wrapped in `.table-scroll` by `base.html` on load**, not in the ~40
  templates that render one. Without a scroll box of its own, one wide table makes the
  whole *page* wider than the screen and then every screen scrolls sideways — the
  header slides away and the app reads as broken. `overflow-x:auto` clips in **both**
  axes, so the wrapper skips any table containing a dropdown that hangs out of a cell
  (the POS medicine search); `data-no-scroll-wrap` is the explicit opt-out.
- **Inputs are 16px under 900px.** Below that iOS zooms the page in on focus and does
  not zoom back out. The rule is written with three `:not()`s deliberately so it
  outranks the page-level `<style>` blocks (`patients/_fields.html` among them) that
  set 14px — do not "simplify" it to `input[type=text]`, which loses to them.
- **Hover effects are gated behind `@media (hover: none)` overrides.** A tap counts as
  a hover on touch and the style then sticks, so tapped cards stay lifted.

`e2e/test_e2e.py::MobileLayoutTest` measures `scrollWidth - innerWidth` at 390px on
seven screens; it fails by 54–128px if the table wrapping is removed. Those tests wait
on elements rather than `networkidle` — every page polls notifications on a timer, so
"the network went quiet" is a race, and that was making the E2E run flaky.

## Keeping this file current

**This file is maintained alongside the code, in the same commit as the change.** It is the
one document an agent or a new developer reads to understand the project, so a stale line
here is worse than no line. Do not defer it to "later" or a separate cleanup commit.

Update it whenever a change touches any of:

- a new role, feature key, or module, or a change to who can reach what
- a new cross-module pipeline or handoff, or a change to an existing one
- anything about tenancy scoping, the fail-open/fail-closed rule, or `TenantManager`
- the database setup, deploy procedure, cron commands, or how tests are run
- a new management command, or a changed/removed one
- a convention or gotcha worth warning the next agent about (a crash cause, a field-name
  trap, an ordering requirement)

Routine work does **not** belong here: bug fixes with no architectural consequence, copy
tweaks, styling, or a list of files that is easy to discover by looking. When something
here turns out to be wrong, correct it rather than appending a contradiction — this file
should never contain two answers to the same question.

## Performance

The app is deployed on a small shared host, so per-request cost is the difference
between usable and sluggish. These things carry that weight; keep them intact.

- **`DEBUG` defaults to `False` when `BASE_DIR` is under `/home/`** (i.e. on the server).
  With `DEBUG=True` Django retains every SQL query in memory for the process's life and
  serves stack traces to users. Never override this to `True` on a deployment.
- **Templates use the cached loader when `DEBUG` is off** (`settings.py`): each template is
  parsed from disk once per process, not once per render. `APP_DIRS` is therefore `False`
  with explicit `loaders` — do not re-add `APP_DIRS: True` (Django refuses both together).
  In `DEBUG` the plain loaders are used so edits show without a restart.
- **SQLite runs in WAL mode** (`saas/signals.py` `_tune_sqlite`, on `connection_created`):
  production (JabraHost / desktop) is SQLite, and WAL + `synchronous=NORMAL` let readers and
  the writer work concurrently instead of locking the whole file. Skipped on Postgres and a
  no-op on the in-memory test DB.
- **The SaaS owner portal's per-tenant stats are grouped aggregates** keyed by `hospital_id`
  (`saas.views._by_hospital`), built into dicts and attached to each hospital in Python — a
  fixed handful of queries no matter how many tenants. Never add a query inside the
  `{% for h in hospitals %}` loop or per-hospital in the view; `saas.tests.SaasDashboardTest`
  asserts the query count does not grow with tenant count.
- **Sidebar badge counts** (`accounts/context_processors.py`) run on *every* page render.
  They are computed only for modules the user can actually see, and cached per user for
  `BADGE_CACHE_SECONDS`. Adding an ungated count puts a query on every page in the app.
- **The notification endpoint** (`accounts/views.get_notifications_latest`) is polled by
  every open browser on a timer. It renders **without** `request=request` on purpose —
  passing the request builds a RequestContext and drags in every context processor,
  taking it from ~2 queries to ~15 per poll.
- **Stock properties** (`Medicine.sellable_quantity` / `batch_quantity` /
  `expired_quantity`) read the medicine's batches. Templates call them once per row via
  `is_low_stock`, so any view listing many medicines must
  `prefetch_related('batches')` — the properties use that cache when present. Without it
  a 500-item catalogue is 1000+ queries per page.

`tests/test_performance.py` puts query-count ceilings on the hot paths. A failure there
almost always means a query moved inside a loop; find that before raising the number.

## Conventions

- Patients and doctors use **`full_name`**, not `name`. `patient.name` / `lead_surgeon.name` raise `AttributeError` at runtime and have shipped as crash bugs before.
- Vitals fields (temperature, pulse) are free-text `CharField`s — wrap any `float()`/`int()` parsing in `try/except`.
- Money-and-stock operations (sale, discharge + bill, surgery + invoice, PO receive) belong in `transaction.atomic()` with `select_for_update()` on the contended row.
- `{# … #}` is a **single-line** comment in Django templates. Spanning one across several
  lines does not comment them out — it prints them, and evaluates any `{{ }}` or `{% %}`
  inside. Use `{% comment %}…{% endcomment %}` for anything multi-line.
- Dates the staff type use **DD/MM/YYYY**, not a native `<input type="date">` — that renders
  in the *browser's* locale, so the same record reads `29/01/2002` at one desk and
  `01/29/2002` at another.
- Do not commit `.claude/settings.local.json`. `desktop/build.bat` and `desktop/launcher.py` have repeatedly shown as deleted in the working tree without being touched — restore them before committing.
