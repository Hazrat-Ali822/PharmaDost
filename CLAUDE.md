# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PharmaDost is a **multi-tenant SaaS Hospital & Pharmacy Management System** built with Django 4.2 (Python 3.10 locally, 3.13 on the production host). Server-rendered Django templates + vanilla JS — no frontend build step, no SPA framework.

One deployment serves many hospitals ("tenants"). The same codebase is also packaged as a **local Windows desktop app** (PyInstaller + waitress + SQLite), which is why data paths are indirected through `DATA_DIR`.

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
| `expiry_alert [--days N]` | Notify pharmacist/admin about near-expiry stock (daily cron) |
| `low_stock_alert` | Notify pharmacist/admin about low stock (daily cron) |
| `reconcile_stock [--fix]` | Repair `Medicine.quantity` drift vs the sum of its `StockBatch` rows (weekly cron) |
| `repair_tenant_orphans` | Fix rows left with `hospital = NULL` |
| `seed_lab`, `import_labs_scans`, `seed_org`, `seed_roles` | Catalog/role seeding |

## Two databases — do not conflate them

This is the single most common source of confusion:

- **Local dev** uses **Supabase PostgreSQL**, via `DATABASE_URL` in `.env`.
- **Production (PythonAnywhere)** uses **local SQLite** at `/home/PharmaDost/PharmaDost/db.sqlite3`. There is no `DATABASE_URL` there — neither in `.env` nor the WSGI file — so `settings.py` falls back to its SQLite default. The `WARNING:root:No DATABASE_URL environment variable set` line in command output on that host is benign and expected.

They are **separate databases**. A migration applied locally is *not* applied in production; it must be run again on the host.

`settings.py` resolves the DB as: SQLite at `DATA_DIR/db.sqlite3` by default, overridden by `DATABASE_URL` (via `dj_database_url`) when present. `.env` is loaded from `BASE_DIR` first, then `DATA_DIR` with `override=True` (the desktop app sets `PHARMADOST_DATA_DIR` to a writable per-user folder).

Tests run against in-memory SQLite via `pharma_mgmt/test_settings.py` because the remote Postgres is too slow and flaky for a test suite. Omitting `--settings=pharma_mgmt.test_settings` will run the suite over the network and may hang.

## Deploying to PythonAnywhere

```bash
cd ~/PharmaDost && git pull
~/PharmaDost/.venv/bin/python manage.py migrate            # only if there are new migrations
~/PharmaDost/.venv/bin/python manage.py collectstatic --noinput   # only if static changed
# then: Web tab → Reload (required; nothing takes effect without it)
```

**`DJANGO_SECRET_KEY` must be in `.env` on the host.** Anything under `/home/` refuses to
start on the built-in default — that key signs session cookies, so a server using the
published one can be logged into as any user by anyone. Set it once:

```bash
cd ~/PharmaDost
python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64))" >> .env
```

Changing it signs everyone out once. Do **not** gate this on `DJANGO_ENV` — nothing sets
that variable on the host, which is why the old check never fired.

**Always use the full path `~/PharmaDost/.venv/bin/python`.** A bare `python` resolves to the system Python 3.13, which lacks `dj_database_url` and fails with `ModuleNotFoundError`.

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

Apps follow this with module-local helpers: `_scoped_prescriptions` / `_scoped_appointments` (prescriptions), `_scoped_orders` (lab), `_scoped_studies` (imaging), `_scoped_admissions` (ipd), `_get_scoped_patient` (patients). Reuse them rather than re-rolling the filter. The sidebar badge counts in `accounts/context_processors.py` use the same `scope_by_hospital = not user.is_superuser` flag.

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

**`AuditLog` is tenant-scoped** (`hospital` FK + `TenantManager`, with `all_objects` for the
superuser portal and commands). It was not, and one hospital's admin could read every other
tenant's trail — patient names, sales, staff sign-ins — from `/audit/`. Entries are filed
under the hospital the *affected object* belongs to, not the actor's, so a superuser editing
a tenant's record still shows up for that tenant's admin. Failed sign-ins are filed by
looking the attempted address up; without that they land under no hospital and the admin
whose staff account is being guessed at never sees them. The filter dropdowns are built
from the scoped queryset too — a list naming another tenant's staff leaks just as surely as
the rows. Guarded by `tests/test_admin_awareness.py::AuditLogIsolationTest`.

### Landing / dashboards

`LOGIN_REDIRECT_URL` → `user_mgmt:post_login_redirect` → `user_mgmt.views.dashboard_router`, which sends superusers to the SaaS portal, ADMINs to `/`, and everyone else to a role template from `ROLE_TEMPLATES`.

`/` maps to `inventory.views.dashboard` (the pharmacy dashboard). It is **not** feature-gated with a hard 403 — users lacking `inventory` are redirected to `post_login_redirect` instead, and `dashboard_router` avoids bouncing back for admins whose pharmacy module is off. Preserve both sides of that guard or you create a redirect loop.

### The front desk (reception → doctor)

`opd/reception/` is where a visit starts. It asks one question — **new patient or old?**

- **New** → `visit_create` renders `PatientForm` *and* `VisitForm` on one screen and one
  submit registers the patient, books the appointment and raises the consultation invoice.
- **Old** → search by MRN, mobile, CNIC or name. CNIC is stored dashed, so the query
  annotates a `Replace`-stripped copy and matches against that; typing the number straight
  off the card has to work.

Both paths land on `appointment_slip` — the printable token slip. Its `@media print` block
hides the whole shell so the slip is the page.

**Departments** (`opd.Department`, tenant-scoped) come first: reception picks the
department, and only that department's doctors are offered. `VisitForm.clean` rejects a
doctor/department mismatch, so the JS filter is convenience, not the guard.

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
- **Lab / imaging → billing**: ordering a test or scan auto-creates a pending `Invoice` via `billing.services.create_service_invoice`.
- **Reorder → purchase order**: `inventory.services.reorder_suggestions()` (sales velocity based) feeds `reorder_to_po`, which creates draft `PurchaseRequest`s grouped by supplier.

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

Two things here are load-bearing:

- The template context key is **`branding`**, not `site` — Django's `LoginView` injects its own `site` variable which would shadow it.
- **Never force `pk=1`** when saving. Inserting an explicit primary key on PostgreSQL desyncs the id sequence and the next tenant's row collides with `duplicate key ... (id)=(1)`. Migration `user_mgmt/0008` resyncs the sequence.

`base.html` injects tenant colours as CSS variables over `app.css`. When editing `static/css/app.css`, bump the `?v=X.X` cache-busting query string in every template that links it.

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
between usable and sluggish. Four things carry that weight; keep them intact.

- **`DEBUG` defaults to `False` when `BASE_DIR` is under `/home/`** (i.e. on the server).
  With `DEBUG=True` Django retains every SQL query in memory for the process's life and
  serves stack traces to users. Never override this to `True` on a deployment.
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
