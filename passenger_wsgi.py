"""Passenger entry point for cPanel / shared hosting (JabraHost et al.).

cPanel's "Setup Python App" serves the site through Phusion Passenger, which looks
for a module named `passenger_wsgi` exposing a WSGI callable called `application`.
PythonAnywhere used its own WSGI file instead, so this did not exist before.

It just re-exports Django's WSGI app; all real configuration stays in settings.py,
which already detects a server by the /home/ path (DEBUG off, SECRET_KEY required).
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pharma_mgmt.settings")

from pharma_mgmt.wsgi import application  # noqa: E402  (must follow the env setup)

# Passenger imports `application` from this module by name.
__all__ = ["application"]
