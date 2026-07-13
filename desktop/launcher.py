"""
PharmaDost — Local Desktop App launcher.

Runs the whole Django system as a self-contained Windows program:
  * stores the database + uploaded files in a per-user folder that survives re-installs
    (%LOCALAPPDATA%\\PharmaDost) — nothing is written into the read-only install dir;
  * applies any pending database migrations automatically on every start;
  * collects static assets once (served by WhiteNoise, no separate web server needed);
  * starts a local web server (waitress) bound to 127.0.0.1 on a free port;
  * opens the app in a native desktop window if pywebview is available, otherwise in
    the default web browser.

This same file is the PyInstaller entry point (see PharmaDost.spec).
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


APP_NAME = "PharmaDost"
HOST = "127.0.0.1"


# --------------------------------------------------------------------------- paths
def install_dir() -> Path:
    """Folder the app is running from — the project root in dev, or the folder the
    bundled .exe lives in when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Writable per-user data folder. Everything the user owns lives here."""
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------- environment
def configure_environment() -> Path:
    """Point Django at the per-user data folder and localhost-friendly settings."""
    root = install_dir()
    # When frozen, PyInstaller unpacks the code to sys._MEIPASS; the project modules
    # (pharma_mgmt, accounts, ...) are bundled there. In dev, add the project root.
    code_root = Path(getattr(sys, "_MEIPASS", root))
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))

    dd = data_dir()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pharma_mgmt.settings")
    os.environ["PHARMADOST_DATA_DIR"] = str(dd)
    os.environ.setdefault("DJANGO_DEBUG", "False")   # no tracebacks to the end user
    os.environ.setdefault("DJANGO_SSL", "false")     # plain http on localhost — keep cookies working
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
    # A stable secret key per install, generated once and stored in the data folder.
    _ensure_secret_key(dd)
    return dd


def _ensure_secret_key(dd: Path) -> None:
    if os.getenv("DJANGO_SECRET_KEY"):
        return
    key_file = dd / "secret.key"
    if key_file.exists():
        os.environ["DJANGO_SECRET_KEY"] = key_file.read_text(encoding="utf-8").strip()
        return
    from django.core.management.utils import get_random_secret_key
    key = get_random_secret_key()
    key_file.write_text(key, encoding="utf-8")
    os.environ["DJANGO_SECRET_KEY"] = key


# ------------------------------------------------------------------- django set-up
def prepare_database() -> None:
    """Apply migrations and collect static files. Safe to run on every launch."""
    import django
    django.setup()
    from django.core.management import call_command

    print("[PharmaDost] Preparing database ...", flush=True)
    call_command("migrate", interactive=False, verbosity=1)

    # Collect static once so WhiteNoise can serve it. Re-running is cheap/idempotent.
    try:
        call_command("collectstatic", interactive=False, verbosity=0)
    except Exception as exc:  # never block startup on a static hiccup
        print(f"[PharmaDost] collectstatic skipped: {exc}", flush=True)


def serve(port: int) -> None:
    """Run the WSGI app under waitress (a production-quality pure-Python server)."""
    from waitress import serve as waitress_serve
    from pharma_mgmt.wsgi import application
    waitress_serve(application, host=HOST, port=port, threads=8, _quiet=True)


def wait_until_up(url: str, timeout: float = 20.0) -> bool:
    import urllib.request
    import urllib.error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.HTTPError:
            return True  # server answered (even a redirect/403) — it's up
        except Exception:
            time.sleep(0.25)
    return False


# --------------------------------------------------------------------------- main
def main() -> None:
    dd = configure_environment()
    prepare_database()

    port = free_port()
    url = f"http://{HOST}:{port}/"

    threading.Thread(target=serve, args=(port,), daemon=True).start()
    wait_until_up(url)

    print(f"\n  {APP_NAME} is running")
    print(f"  Open:  {url}")
    print(f"  Data:  {dd}")
    print("  Close this window to stop the app.\n", flush=True)

    # Prefer a native desktop window; fall back to the default browser.
    try:
        import webview  # pywebview
        webview.create_window(APP_NAME, url, width=1280, height=800)
        webview.start()
    except Exception:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
