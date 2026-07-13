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

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() == "true"
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

if os.getenv("DJANGO_ENV") == "production" and not os.getenv("DJANGO_SECRET_KEY"):
    raise RuntimeError("DJANGO_SECRET_KEY is required in production")

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
    "user_mgmt",
    "audit",
    "sales.apps.SalesConfig",
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


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],   # <-- this needs BASE_DIR above!
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.nav_permissions",
                "accounts.context_processors.site_branding",
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
db_env = dj_database_url.config(conn_max_age=600)
if db_env:
    DATABASES["default"] = db_env

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

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
    'user_mgmt.middleware.SetupMiddleware',
    'user_mgmt.middleware.LoginRequiredMiddleware',
]
ROOT_URLCONF = "pharma_mgmt.urls"

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