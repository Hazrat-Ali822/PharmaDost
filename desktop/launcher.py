"""
PharmaDost / Sehatyar — Local Desktop App launcher.

Runs the whole Django system as a self-contained Windows program:
  * stores the database + uploaded files in a per-user folder that survives re-installs
    (%LOCALAPPDATA%\\PharmaDost) — nothing is written into the read-only install dir;
  * applies any pending database migrations automatically on every start;
  * collects static assets once (served by WhiteNoise, no separate web server needed);
  * starts a local web server (waitress);
  * opens the app in a native desktop window if pywebview is available, otherwise in
    the default web browser.

LAN MODE (on by default)
------------------------
The server binds to **0.0.0.0**, not just localhost, so every phone and computer on
the same wifi can use this one machine as the hospital's server. That is the whole
point in places where the internet is down most of the day: the server is in the
room, so nothing depends on the uplink — notifications, the doctor↔reception
handoff, live stock, all of it works exactly as it does online, because from the
app's point of view it *is* online.

Two things this requires and does:
  * the LAN address must be in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, or Django
    answers "Bad Request (400)" / rejects every form — both are set from the detected
    addresses below;
  * Windows Firewall blocks incoming connections by default, so the launcher adds an
    inbound rule for the port (needs admin; if it cannot, it says so plainly instead
    of leaving staff to guess why their phones cannot connect).

Set `PHARMADOST_LAN=0` to go back to localhost-only.

The port is **fixed** (default 8000) rather than random: staff type this address into
a phone once and bookmark it, and a new port every morning would break that.

This same file is the PyInstaller entry point (see PharmaDost.spec).
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


APP_NAME = "PharmaDost"
BRAND = "Sehatyar"
LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 8000
FIREWALL_RULE = "Sehatyar HMS (LAN)"


def lan_enabled() -> bool:
    return os.getenv("PHARMADOST_LAN", "1").lower() not in ("0", "false", "no", "off")


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


# ----------------------------------------------------------------------- networking
def lan_addresses() -> list:
    """Every IPv4 address this machine can be reached on from the local network.

    The first entry is the one the operating system would actually use — found by
    opening a UDP socket toward a public address, which sends **no packets** and
    needs **no internet**; it only asks the routing table which interface would be
    chosen. On a clinic router with a dead uplink there is still a default route, so
    this keeps working. `gethostbyname_ex` is the fallback and also catches a second
    interface (wifi + ethernet), because the staff may be on either one.
    """
    found = []

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        found.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except Exception:
        pass

    # Loopback is not a LAN address; a 169.254.x.x is Windows saying "no DHCP".
    return [ip for ip in found
            if not ip.startswith("127.") and not ip.startswith("169.254.")]


def pick_port(host: str, preferred: int = DEFAULT_PORT) -> int:
    """Keep the same port every launch when we can — the address staff typed into a
    phone yesterday has to still work today. Only move if something else has it."""
    for port in [preferred, 8080, 8800, 5000]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, port))
            s.close()
            return port
        except OSError:
            continue
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # last resort: any free one
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    print(f"[{BRAND}] WARNING: ports 8000/8080/8800/5000 are all in use — using "
          f"{port} this time. Phones bookmarked to the old address will need the "
          f"new one (shown below and on the Connect a Device page).", flush=True)
    return port


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def open_firewall(port: int) -> str:
    """Let other devices on the wifi reach this port.

    Windows Firewall drops incoming connections silently, so without this the phones
    just time out and nobody can tell whether the address is wrong, the wifi is wrong
    or the app is down. Returns a human-readable status the startup banner prints —
    never raises, and never blocks the app from starting.
    """
    if os.name != "nt":
        return "not Windows — no firewall rule needed"
    if not _is_admin():
        return ("SKIPPED — not running as administrator. If phones cannot connect, "
                "right-click the app and 'Run as administrator' once.")
    try:
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        # Replace any rule from a previous run — the port may have changed.
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        f"name={FIREWALL_RULE}"],
                       capture_output=True, timeout=15, **flags)
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={FIREWALL_RULE}", "dir=in", "action=allow", "protocol=TCP",
             # profile=any covers Public too: Windows marks a new/unknown clinic
             # wifi Public by default, and a rule scoped to private,domain would
             # silently not apply — phones time out while the banner says "allowed".
             f"localport={port}", "profile=any"],
            capture_output=True, timeout=15, **flags)
        if result.returncode == 0:
            return f"allowed on port {port}"
        return f"could not be added (netsh exit {result.returncode})"
    except Exception as exc:
        return f"could not be added ({exc})"


# --------------------------------------------------------------------- environment
def configure_environment(host_names, port) -> Path:
    """Point Django at the per-user data folder and at the addresses it will answer on."""
    root = install_dir()
    # When frozen, PyInstaller unpacks the code to sys._MEIPASS; the project modules
    # (pharma_mgmt, accounts, ...) are bundled there. In dev, add the project root.
    code_root = Path(getattr(sys, "_MEIPASS", root))
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))

    dd = data_dir()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pharma_mgmt.settings")
    os.environ["PHARMADOST_DATA_DIR"] = str(dd)
    os.environ["PHARMADOST_DESKTOP"] = "1"           # turns on offline licence enforcement
    os.environ.setdefault("DJANGO_DEBUG", "False")   # no tracebacks to the end user
    os.environ.setdefault("DJANGO_SSL", "false")     # plain http on the LAN — secure-only cookies would break login

    # Django refuses a request whose Host header is not listed, and rejects a POST
    # whose Origin is not trusted. Both must name the LAN address, or the phones get
    # "Bad Request (400)" and every form fails CSRF.
    os.environ["DJANGO_ALLOWED_HOSTS"] = ",".join(host_names)
    os.environ["DJANGO_CSRF_TRUSTED"] = ",".join(
        f"http://{h}:{port}" for h in host_names if h != "*")

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

    print(f"[{BRAND}] Preparing database ...", flush=True)
    call_command("migrate", interactive=False, verbosity=1)

    # Static is already collected into the bundle at build time; re-collecting on a
    # frozen install would only try to write into the read-only install dir and fail.
    # Collect only in dev, where BASE_DIR is writable.
    if not getattr(sys, "frozen", False):
        try:
            call_command("collectstatic", interactive=False, verbosity=0)
        except Exception as exc:  # never block startup on a static hiccup
            print(f"[{BRAND}] collectstatic skipped: {exc}", flush=True)


def _rotate_backups(folder: Path, keep: int) -> None:
    for old in sorted(folder.glob("sehatyar-backup-*.zip"))[:-keep]:
        try:
            old.unlink()
        except Exception:
            pass


def backup_on_start(dd: Path) -> None:
    """Snapshot the database + uploaded files on every launch, so a lost, stolen or
    failed clinic computer does not take the records with it. A PC started each
    morning is therefore backed up daily.

    Copies go to ``<data>/backups`` and, if ``PHARMADOST_BACKUP_DIR`` is set (a USB
    stick, a second disk, or a cloud-synced folder like OneDrive/Google Drive), to
    there as well. That off-machine copy is the one that survives the computer being
    stolen — the local one does not, so setting an external folder is the point.
    """
    import zipfile
    from datetime import datetime

    db = dd / "db.sqlite3"
    if not db.exists():
        return       # first run, nothing to back up yet
    media = dd / "media"
    name = f"sehatyar-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

    targets = [dd / "backups"]
    external = os.getenv("PHARMADOST_BACKUP_DIR")
    if external:
        targets.append(Path(external))

    made = []
    for folder in targets:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / name
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(db, "db.sqlite3")
                if media.exists():
                    for f in media.rglob("*"):
                        if f.is_file():
                            z.write(f, str(Path("media") / f.relative_to(media)))
            _rotate_backups(folder, keep=14)
            made.append(out)
        except Exception as exc:
            print(f"[{BRAND}] backup to {folder} skipped: {exc}", flush=True)
    if made:
        extra = f"  (+{len(made) - 1} off-machine)" if len(made) > 1 else \
                "  (set PHARMADOST_BACKUP_DIR to a USB/cloud folder for an off-machine copy)"
        print(f"[{BRAND}] Backup saved: {made[0]}{extra}", flush=True)


def serve(bind_host: str, port: int) -> None:
    """Run the WSGI app under waitress (a production-quality pure-Python server).

    Threads are sized for a small clinic on one wifi — a handful of devices, each
    polling notifications every 30s. SQLite is in WAL mode (`saas.signals._tune_sqlite`),
    so those readers do not block the one writer.
    """
    from waitress import serve as waitress_serve
    from pharma_mgmt.wsgi import application
    try:
        waitress_serve(application, host=bind_host, port=port, threads=16, _quiet=True)
    except Exception as exc:
        # This runs in a daemon thread, so an unhandled bind error would otherwise
        # die silently and the window would open onto a dead address. Say so loudly.
        print(f"\n[{BRAND}] SERVER FAILED TO START on {bind_host}:{port} — {exc}\n"
              f"Close the app and try again; if it persists another program may be "
              f"using the port.", flush=True)


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


def banner(dd, local_url, lan_url, ips, port, firewall) -> None:
    line = "=" * 66
    print(f"\n{line}")
    print(f"  {BRAND} is running")
    print(line)
    print(f"  On this computer : {local_url}")
    if lan_url:
        print(f"  On phones/tablets: {lan_url}")
        if len(ips) > 1:
            print(f"                     (other addresses: "
                  f"{', '.join(f'http://{i}:{port}/' for i in ips[1:])})")
        print(f"  Firewall         : {firewall}")
        print("")
        print("  Every device must be on the SAME wifi. Internet is not needed.")
        print(f"  A phone opens the address above in Chrome — {BRAND} works there")
        print("  fully: notifications, tokens, stock, everything, live.")
    else:
        print("  LAN mode is OFF — only this computer can use the app.")
    print("")
    print(f"  Data folder      : {dd}")
    print("  Closing this window stops the app for everyone.")
    print(f"{line}\n", flush=True)


# --------------------------------------------------------------------------- main
def main() -> None:
    use_lan = lan_enabled()
    ips = lan_addresses() if use_lan else []
    bind_host = "0.0.0.0" if use_lan else LOCALHOST
    # Probe the port on the interface we will actually bind (0.0.0.0 in LAN mode),
    # not loopback — a port free on localhost but held on another interface would
    # otherwise pass the check and then fail when the real server binds.
    port = pick_port(bind_host, int(os.getenv("PHARMADOST_PORT", DEFAULT_PORT)))

    hosts = [LOCALHOST, "localhost"] + ips
    try:
        hosts.append(socket.gethostname())
    except Exception:
        pass

    dd = configure_environment(hosts, port)

    local_url = f"http://{LOCALHOST}:{port}/"
    lan_url = f"http://{ips[0]}:{port}/" if ips else ""
    # The in-app "Connect a device" page reads these to show the address + QR code.
    os.environ["PHARMADOST_LAN_URL"] = lan_url
    os.environ["PHARMADOST_LAN_IPS"] = ",".join(ips)

    prepare_database()
    backup_on_start(dd)

    firewall = open_firewall(port) if use_lan else "LAN mode off"

    threading.Thread(target=serve, args=(bind_host, port), daemon=True).start()
    if not wait_until_up(local_url):
        print(f"[{BRAND}] WARNING: the server did not come up within 20s. The window "
              f"may show an error page — check any message printed above for the cause.",
              flush=True)

    banner(dd, local_url, lan_url, ips, port, firewall)

    # Prefer a native desktop window; fall back to the default browser.
    try:
        import webview  # pywebview
        webview.create_window(BRAND, local_url, width=1280, height=800)
        webview.start()
    except Exception:
        webbrowser.open(local_url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
