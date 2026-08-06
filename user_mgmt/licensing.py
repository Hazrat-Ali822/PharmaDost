"""Offline licence verification for the Sehatyar desktop / clinic-LAN build.

The hosted SaaS site gates a tenant on `Hospital.expiry_date` server-side
(`saas.middleware.HospitalSubscriptionMiddleware`). The desktop build has no server
to phone home to — it runs in a clinic that may have no internet for days — so its
monthly subscription is enforced with a **signed licence key** the owner hands over
each period.

  * The owner signs a key with the PRIVATE key (`licensing/sign_license.py`, which
    reads `licensing/private_key.json` — never committed).
  * The app carries only the PUBLIC key below and *verifies* the signature. It can
    check a key but never mint one, so a public code repository leaks nothing that
    lets a clinic forge or extend its own licence.
  * Verification is pure standard library (RSA via built-in big-int `pow`), so the
    PyInstaller bundle gains no crypto dependency.

This module lives inside the `user_mgmt` app (not a top-level package) so the desktop
build bundles it automatically alongside every other app module. The owner-side tools
in `licensing/` (keygen, sign_license) import `make_token` from here — one definition
of the token format, used to both sign and verify.

A fresh install runs on a short **trial** so the clinic can start immediately; when
it lapses the app locks (every device on the LAN, since they all go through this one
server) until a valid key is pasted in Settings → Licence.

Clock-rollback is blunted with a stored `last_seen`: winding the PC clock back to
dodge an expiry is treated as `today = max(today, last_seen)`.
"""
import base64
import hashlib
import json
from datetime import date

# Output of `python licensing/keygen.py`. This is the PUBLIC half — safe to ship and
# commit. The matching private key stays in licensing/private_key.json (git-ignored).
PUBLIC_KEY = {
    "e": 65537,
    "n": 19266739330695818242546405892717218302987237657854237987813790766414088496023845797349265200431967767028245762636256899843988031851454216573987341907439734108980546164301794245623331188855070224232772840328736645131373980749720814950631620173016250499725615701010840396479164821671547411301819026135047737905332330251709055369835160016367568465786397098628622337964117711103236215375045216596392362420672367147419293663818174524721581594471700718721495762018399799950074302176648020726836315212351123567503814451219031314839028444894840587085439308509739789548609764979249671568552940755535544829428321477057475267523,
}

TRIAL_DAYS = 14
WARN_DAYS = 5          # show "expires soon" from this many days out
_PREFIX = b"sehatyar-license-v1"
_STATE_FILE = "license.json"


# --------------------------------------------------------------------- crypto
def _digest_int(payload: bytes) -> int:
    return int.from_bytes(hashlib.sha256(_PREFIX + b":" + payload).digest(), "big")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: bytes, priv: dict) -> str:
    """Sign a payload with the private key → a signature string. Owner-side only."""
    sig = pow(_digest_int(payload), priv["d"], priv["n"])
    return _b64e(sig.to_bytes((sig.bit_length() + 7) // 8, "big"))


def make_token(clinic: str, exp: date, iss: date, priv: dict, extra: dict = None) -> str:
    data = {"clinic": clinic, "exp": exp.isoformat(), "iss": iss.isoformat(), "v": 1}
    if extra:
        data.update(extra)      # e.g. {"slug": "shaheen"} to bind the key to one tenant
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return _b64e(payload) + "." + sign(payload, priv)


def read_token(token: str, pub: dict = None) -> dict:
    """Verify a key string and return its payload dict, or None if it is invalid,
    tampered with, or signed by the wrong key."""
    pub = pub or PUBLIC_KEY
    try:
        payload_s, sig_s = token.strip().split(".")
        payload = _b64d(payload_s)
        sig = int.from_bytes(_b64d(sig_s), "big")
        if pow(sig, pub["e"], pub["n"]) != _digest_int(payload):
            return None
        data = json.loads(payload)
        date.fromisoformat(data["exp"])      # shape check
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------- state
def _state_path(data_dir):
    from pathlib import Path
    return Path(data_dir) / _STATE_FILE


def _load_state(data_dir) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data_dir, state: dict) -> None:
    try:
        _state_path(data_dir).write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass       # a read-only disk must never crash the request


def save_license(data_dir, token: str) -> bool:
    """Store a pasted key after verifying it. Returns True if it was accepted."""
    if read_token(token) is None:
        return False
    state = _load_state(data_dir)
    state["token"] = token.strip()
    _save_state(data_dir, state)
    return True


def _effective_today(data_dir, today: date) -> date:
    """`today`, but never earlier than the last day we saw — so winding the PC clock
    back to escape an expiry or refresh a trial does not work. Advances the stored
    marker at most once per day."""
    state = _load_state(data_dir)
    seen = state.get("last_seen")
    seen_date = None
    if seen:
        try:
            seen_date = date.fromisoformat(seen)
        except Exception:
            seen_date = None
    eff = today if (seen_date is None or today >= seen_date) else seen_date
    if seen != eff.isoformat():
        state["last_seen"] = eff.isoformat()
        _save_state(data_dir, state)
    return eff


def license_state(data_dir, today: date = None) -> dict:
    """The single source of truth for whether the desktop build may run.

    Returns a dict: ``ok`` (may the app be used), ``status`` (licensed / trial /
    expired / locked), ``days_left``, ``exp`` (date or None), ``clinic``, ``warn``.
    """
    today = today or date.today()
    eff = _effective_today(data_dir, today)
    state = _load_state(data_dir)

    token = state.get("token")
    if token:
        data = read_token(token)
        if data:
            exp = date.fromisoformat(data["exp"])
            days = (exp - eff).days
            if days >= 0:
                return {"ok": True, "status": "licensed", "days_left": days,
                        "exp": exp, "clinic": data.get("clinic", ""),
                        "warn": days <= WARN_DAYS}
            return {"ok": False, "status": "expired", "days_left": days,
                    "exp": exp, "clinic": data.get("clinic", ""), "warn": True}
        # A stored key that no longer verifies (corrupt / wrong build) falls through
        # to the trial rather than locking outright.

    # No valid key: run the trial, dated from the first time we ever checked.
    start = state.get("trial_start")
    if not start:
        start = eff.isoformat()
        state["trial_start"] = start
        _save_state(data_dir, state)
    try:
        start_date = date.fromisoformat(start)
    except Exception:
        start_date = eff
    left = TRIAL_DAYS - (eff - start_date).days
    if left >= 0:
        return {"ok": True, "status": "trial", "days_left": left, "exp": None,
                "clinic": "", "warn": True}
    return {"ok": False, "status": "locked", "days_left": left, "exp": None,
            "clinic": "", "warn": True}
