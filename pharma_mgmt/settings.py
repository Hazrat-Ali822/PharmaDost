import os
from pathlib import Path
from dotenv import load_dotenv

# ------------------------
# BASE_DIR + .env loading
# ------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------
# Data directory
# When packaged as a local desktop app, the launcher sets PHARMADOST_DATA_DIR to a
# writable per-user folder (e.g. %LOCALAPPDATA%\PharmaDost) so the database, media
# and .env live OUTSIDE the read-only install directory and survive re-installs.
# On the web/dev setup it is unset, so everything stays in the project root as before.
# ------------------------
DATA_DIR = Path(os.getenv("PHARMADOST_DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load .env from the project root explicitly (works no matter the CWD, e.g. under
# the PythonAnywhere WSGI server where the working directory isn't the project).
load_dotenv(BASE_DIR / ".env")
# A per-user .env in the data dir can override (used by the desktop app).
load_dotenv(DATA_DIR / ".env", override=True)

# The desktop / clinic-LAN build sets this (see desktop/launcher.py). It switches on
# offline licence enforcement (licensing/, DesktopLicenseMiddleware): the hosted SaaS
# site gates tenants by Hospital.expiry_date instead and leaves this False, so the
# licence middleware is a no-op there.
DESKTOP_BUILD = os.getenv("PHARMADOST_DESKTOP", "0").lower() in ("1", "true", "yes", "on")

_INSECURE_SECRET_KEY = "dev-secret-key-change-me"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _INSECURE_SECRET_KEY)

# DEBUG defaults True for local development, but NOT on a server. On PythonAnywhere
# the project lives under /home/<user>/ and there is no .env setting this, so the old
# unconditional "True" default meant production ran with DEBUG on: stack traces
# containing source and settings were served to real users, and Django retained every
# SQL query in memory for the life of the process (a steady slowdown). Set
# DJANGO_DEBUG explicitly to override either way.
_looks_like_a_server = str(BASE_DIR).startswith("/home/")
DEBUG = os.getenv("DJANGO_DEBUG", "False" if _looks_like_a_server else "True").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h]

# Render Cloud Platform integration
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if render_host:
    ALLOWED_HOSTS.append(render_host)

# PythonAnywhere integration
if str(BASE_DIR).startswith("/home/"):
    parts = str(BASE_DIR).split("/")
    if len(parts) > 2:
        username = parts[2]
        ALLOWED_HOSTS.append(f"{username}.pythonanywhere.com")
        ALLOWED_HOSTS.append(f"*.pythonanywhere.com")

# HTTPS origins allowed to POST (needed for the pythonanywhere domain on Django 4.2)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED", "").split(",") if o.strip()]
if render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_host}")
if str(BASE_DIR).startswith("/home/"):
    parts = str(BASE_DIR).split("/")
    if len(parts) > 2:
        username = parts[2]
        CSRF_TRUSTED_ORIGINS.append(f"https://{username}.pythonanywhere.com")
        CSRF_TRUSTED_ORIGINS.append(f"https://*.pythonanywhere.com")

# Multi-tenant subdomains — each hospital is reachable at <slug>.<BASE_DOMAIN>
# (e.g. shaheen-health-care.sehatyar.online), while the bare platform domain is
# reserved for the SaaS owner + public demo. The wildcard host/origin below let
# every tenant subdomain answer and POST; wildcard DNS (*.<domain>) and a
# wildcard TLS cert must be set up on the host for these to resolve in a browser.
BASE_DOMAIN = os.getenv("PHARMADOST_BASE_DOMAIN", "sehatyar.online").strip().lower()
if BASE_DOMAIN and "." in BASE_DOMAIN and BASE_DOMAIN != "localhost":
    for _h in (BASE_DOMAIN, f".{BASE_DOMAIN}"):     # '.domain' matches all subdomains
        if _h not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_h)
    for _o in (f"https://{BASE_DOMAIN}", f"https://*.{BASE_DOMAIN}"):
        if _o not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_o)

# The key that signs session cookies and password-reset tokens. Running a server
# on the published default lets anyone forge a cookie and sign in as any user, so
# this refuses to start rather than serve.
#
# Keyed on the same "am I on a server" signal as DEBUG above, NOT on DJANGO_ENV:
# nothing sets DJANGO_ENV on the PythonAnywhere host (there is no DATABASE_URL
# there either), so the old check could never fire where it mattered.
if _looks_like_a_server and SECRET_KEY == _INSECURE_SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY is not set. Add a long random value to .env in the "
        "project root before starting the server:\n"
        "  python -c \"import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64))\" >> .env\n"
        "Changing it signs everyone out once; leaving it at the default lets "
        "anyone forge a login."
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "saas",
    "accounts",
    "suppliers",
    "inventory",
    "lab",
    "imaging",
    "patients",
    "opd",
    "billing",
    "prescriptions",
    "customers",
    "panels",
    "user_mgmt",
    "audit",
    "sales.apps.SalesConfig",
    "ipd",
    "ot",
    "emergency",
    "hr",
    "maternity",
    "diagnosis",
    "referral",
    "certificates",
    "bloodbank",
    "vaccination",
    "consent",
    "offline_sync",
    "messaging",
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = 'user_mgmt:post_login_redirect' # role router (view below)
LOGOUT_REDIRECT_URL = '/login/'

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "saas.middleware.TenantMiddleware",
    "audit.middleware.CurrentUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "saas.middleware.HospitalSubscriptionMiddleware",
]


# In production (DEBUG off, i.e. on the server) wrap the loaders in the cached
# loader so each template is parsed from disk once per process, not once per
# render. In DEBUG we keep the plain loaders so edits show up without a restart.
# APP_DIRS must stay False whenever `loaders` is set explicitly.
_TEMPLATE_LOADERS = [
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]
if not DEBUG:
    _TEMPLATE_LOADERS = [("django.template.loaders.cached.Loader", _TEMPLATE_LOADERS)]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],   # <-- this needs BASE_DIR above!
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": _TEMPLATE_LOADERS,
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.nav_permissions",
                "accounts.context_processors.site_branding",
                "accounts.context_processors.notifications_context",
            ],
        },
    },
]

WSGI_APPLICATION = "pharma_mgmt.wsgi.application"

import dj_database_url

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

# If DATABASE_URL is set in environment/env, use PostgreSQL (Supabase)
db_env = dj_database_url.config(conn_max_age=0)
if db_env:
    DATABASES["default"] = db_env

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Outbound messaging (see messaging/services.py)
#
# Both channels are OFF unless configured, and `messaging.services` records that
# as SKIPPED rather than an error — most installs have no SMS gateway and the
# desktop/LAN build has no internet at all. Nothing here may make a send raise.
# ---------------------------------------------------------------------------
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "").strip()
EMAIL_PORT = int(os.getenv("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_USER", "").strip()
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("DJANGO_EMAIL_TLS", "True").lower() == "true"
EMAIL_USE_SSL = os.getenv("DJANGO_EMAIL_SSL", "False").lower() == "true"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_EMAIL_FROM", EMAIL_HOST_USER).strip()
# With no host configured, print to the console instead of attempting SMTP —
# in development that shows the message, and in production `email_configured()`
# is False so nothing is attempted at all.
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST
                 else "django.core.mail.backends.console.EmailBackend")

# SMS: any HTTP gateway, described entirely by env so switching vendor is a
# change to .env rather than to code. {to} and {text} are substituted.
#   PHARMADOST_SMS_URL=https://api.example.pk/send
#   PHARMADOST_SMS_PARAMS=api_key=abc&sender=Sehatyar&to={to}&text={text}
SMS_URL = os.getenv("PHARMADOST_SMS_URL", "").strip()
SMS_PARAMS = os.getenv("PHARMADOST_SMS_PARAMS", "to={to}&text={text}").strip()
SMS_METHOD = os.getenv("PHARMADOST_SMS_METHOD", "GET").strip()

# ---------------------------------------------------------------------------
# Failed-login lockout (accounts.lockout)
# ---------------------------------------------------------------------------
LOCKOUT_THRESHOLD = int(os.getenv("PHARMADOST_LOCKOUT_THRESHOLD", "8"))
LOCKOUT_WINDOW_MINUTES = int(os.getenv("PHARMADOST_LOCKOUT_WINDOW", "15"))
LOCKOUT_MINUTES = int(os.getenv("PHARMADOST_LOCKOUT_MINUTES", "15"))

# ---------------------------------------------------------------------------
# Logging + error reporting
#
# With DEBUG off, an unhandled exception on the host produced a 500 page and
# nothing else — no trace, no file, no alert — so the first anyone knew of a
# broken screen was a customer telephoning. These write the traceback somewhere
# a person can read it.
# ---------------------------------------------------------------------------
LOG_DIR = DATA_DIR / "logs"
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_file = str(LOG_DIR / "app.log")
except OSError:                      # read-only install dir; console only
    _log_file = None

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "full": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "full"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR",
                           "propagate": False},
    },
}
if _log_file:
    LOGGING["handlers"]["file"] = {
        # Rotating, because a shared host's disk quota is small and an
        # unbounded log is its own outage.
        "class": "logging.handlers.RotatingFileHandler",
        "filename": _log_file,
        "maxBytes": 2 * 1024 * 1024,
        "backupCount": 3,
        "formatter": "full",
    }
    LOGGING["root"]["handlers"].append("file")
    LOGGING["loggers"]["django.request"]["handlers"].append("file")

# Sentry is entirely optional — the package is not in requirements.txt, so an
# install without it (and every install without a DSN set) simply carries on.
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            # Errors only by default: performance tracing on a small shared host
            # costs more than it tells you, and every trace is an outbound call.
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0")),
            # These carry patient data. Never ship it to a third party.
            send_default_pii=False,
            environment=os.getenv("SENTRY_ENV", "production"),
        )
    except Exception:                # pragma: no cover - never block startup
        pass

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"

# WhiteNoise lets any WSGI server (waitress in the desktop app, or the PythonAnywhere
# worker) serve the collected static files directly — no separate web server needed.
# Guarded so the project still runs if the package isn't installed (plain dev/runserver).
try:
    import whitenoise  # noqa: F401
    _WHITENOISE = True
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
except ImportError:
    _WHITENOISE = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"


# WhiteNoise must sit directly after SecurityMiddleware so it can serve static assets.
if _WHITENOISE:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

# Custom middleware: force first-run setup on a fresh install, then enforce login
MIDDLEWARE += [
    # Stamps the `dv` cookie so a bfcache Back can tell it is showing pre-edit
    # data and re-fetch (see DataVersionMiddleware). Cheap and read by base.html.
    'user_mgmt.middleware.DataVersionMiddleware',
    'user_mgmt.middleware.SetupMiddleware',
    'user_mgmt.middleware.LoginRequiredMiddleware',
    # Offline licence lock — active only on the desktop/LAN build (DESKTOP_BUILD).
    # A no-op on the hosted site, so it is harmless in the list everywhere.
    'user_mgmt.middleware.DesktopLicenseMiddleware',
]
ROOT_URLCONF = "pharma_mgmt.urls"

# ------------------------
# Session lifetime — sized for sites that lose the internet for days.
# ------------------------
# A device with no connection cannot sign in: the login page needs the server. So a
# session that quietly expires mid-outage locks the desk out of a system that would
# otherwise have kept working from cache and the offline outbox. Django's two-week
# default is short for a clinic that is offline for a week at a time.
#
# `SESSION_SAVE_EVERY_REQUEST` stays off on purpose — it would write the session row
# on every request, which is real cost on a small shared host for little gain here.
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_DAYS", "30")) * 24 * 60 * 60

# ------------------------
# HTTPS hardening — only when actually served over HTTPS (e.g. PythonAnywhere).
# The desktop app runs over plain http://127.0.0.1, where secure-only cookies would
# stop login working, so it sets DJANGO_SSL=false. Default: on whenever DEBUG is off,
# so the existing PythonAnywhere setup keeps its hardening with no .env change.
# ------------------------
_ssl_env = os.getenv("DJANGO_SSL")
USE_SSL = _ssl_env.lower() in ("1", "true", "yes") if _ssl_env is not None else (not DEBUG)
if USE_SSL:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # Send plain http straight to https instead of serving the app there.
    # Typing the bare domain on a phone lands on http, and that address is a
    # dead end for this app in two ways at once: browsers refuse to register a
    # service worker on an insecure origin (so nothing works offline), and the
    # cookies above are Secure-only (so a sign-in never sticks). Neither failure
    # says anything on screen.
    # Env-overridable because it is the one setting here that can take a site
    # down rather than merely weaken it: a host that does not tell Django the
    # request arrived over TLS would redirect to https for ever. Set
    # DJANGO_SSL_REDIRECT=false in .env if that happens.
    SECURE_SSL_REDIRECT = os.getenv(
        "DJANGO_SSL_REDIRECT", "true").lower() in ("1", "true", "yes")