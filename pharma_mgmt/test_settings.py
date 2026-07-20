"""Fast, reliable test settings: run the suite on an in-memory SQLite DB instead of
the remote Postgres (which is slow/flaky for CI-style runs over the network).

    python manage.py test --settings=pharma_mgmt.test_settings
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Fast password hashing for tests
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
