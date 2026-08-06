# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the Sehatyar / PharmaDost desktop app.

Run from the PROJECT ROOT via `desktop\\build.bat` (which does collectstatic first,
then `pyinstaller desktop\\PharmaDost.spec`). Produces `dist\\PharmaDost\\PharmaDost.exe`.

Django loads apps, templates, template tags and migrations dynamically (by name), so
PyInstaller cannot discover them from imports alone. Every local app's submodules are
listed as hidden imports, and each app's own templates + migration files are bundled
as data (collected straight off disk — importing the apps here would need a full
Django set-up). All static lives in `staticfiles/` after collectstatic, so that one
folder covers every app's assets. Paths are absolute (`ROOT`) because PyInstaller
resolves relative paths against the spec's folder, not the working directory.
"""
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pharma_mgmt.settings")
ROOT = os.path.abspath(os.getcwd())

# PyInstaller runs this spec with its own sys.path, which does NOT include the project
# root — so `collect_submodules('pharma_mgmt')` and the app packages could not be
# imported and silently returned nothing, leaving the settings/app code out of the
# bundle. Put the project root on the path so every local package is discoverable.
sys.path.insert(0, ROOT)

# The Django project package + every local app in INSTALLED_APPS.
APPS = [
    "pharma_mgmt",
    "saas", "accounts", "suppliers", "inventory", "lab", "imaging", "patients",
    "opd", "billing", "prescriptions", "customers", "panels", "user_mgmt", "audit",
    "sales", "ipd", "ot", "emergency", "hr", "maternity", "diagnosis", "referral",
    "certificates", "bloodbank", "vaccination", "consent", "offline_sync",
]

# --- hidden imports: all app submodules (views, migrations, templatetags, …) + deps
hiddenimports = []
for app in APPS:
    hiddenimports += collect_submodules(app)
hiddenimports += collect_submodules("django")
hiddenimports += collect_submodules("whitenoise")   # incl. whitenoise.storage (STORAGES)
hiddenimports += [
    # settings/urls/wsgi are imported by string (DJANGO_SETTINGS_MODULE), never as a
    # static import, so name them explicitly as a safety net.
    "pharma_mgmt.settings", "pharma_mgmt.urls", "pharma_mgmt.wsgi",
    "whitenoise.storage",
    "waitress", "dj_database_url", "dotenv", "sqlite3", "PIL", "PIL.Image", "qrcode",
    "webview", "clr_loader", "cffi",       # pywebview desktop window (Windows)
]

# --- data: top-level templates + collected static, plus each app's templates/migrations
datas = [
    (os.path.join(ROOT, "templates"), "templates"),
    (os.path.join(ROOT, "staticfiles"), "staticfiles"),
]
for app in APPS:
    for sub in ("templates", "migrations"):
        d = os.path.join(ROOT, app, sub)
        if os.path.isdir(d):
            datas.append((d, app + "/" + sub))

_icon = os.path.join(ROOT, "desktop", "icon.ico")
icon = _icon if os.path.exists(_icon) else None

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PharmaDost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # the startup window prints the LAN address staff read off it
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PharmaDost",
)
