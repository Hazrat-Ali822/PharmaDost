"""Generate the Sehatyar license signing keypair — run ONCE, by the SaaS owner.

    python licensing/keygen.py

Writes the **private** key to `licensing/private_key.json` (git-ignored — this is
your signing secret, never commit it or share it) and prints the **public** key to
paste into `user_mgmt/licensing.py::PUBLIC_KEY`.

The app ships only the public key: it can *verify* a licence but can never *make*
one, so even though this repository is public nobody can forge a key or extend
their own subscription. Only the holder of `private_key.json` (you) can sign.

Pure standard-library RSA (2048-bit) so the desktop build needs no crypto package
to bundle.
"""
import json
import secrets
from pathlib import Path

_SMALL_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]


def _probable_prime(n, rounds=40):
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _probable_prime(n):
            return n


def generate(bits=2048):
    e = 65537
    half = bits // 2
    while True:
        p, q = _gen_prime(half), _gen_prime(half)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return {"n": n, "e": e, "d": d}


if __name__ == "__main__":
    key = generate()
    out = Path(__file__).resolve().parent / "private_key.json"
    out.write_text(json.dumps({"n": key["n"], "e": key["e"], "d": key["d"]}),
                   encoding="utf-8")
    print("Private key written to:", out)
    print("KEEP IT SAFE and NEVER COMMIT IT.\n")
    print("Paste this into user_mgmt/licensing.py as PUBLIC_KEY:\n")
    print("PUBLIC_KEY = {")
    print(f'    "e": {key["e"]},')
    print(f'    "n": {key["n"]},')
    print("}")
