"""Issue a Sehatyar licence key — run by the SaaS owner, on the machine that holds
`private_key.json`. This is how you collect the monthly subscription for a clinic
running the offline desktop / LAN build.

    python licensing/sign_license.py "Shaheen Clinic" 1
    python licensing/sign_license.py "Al-Shifa Hospital" 12   # a year

Arguments: the clinic name (as it should read on their licence) and the number of
months to grant. Prints a licence key — send it to the clinic (WhatsApp is fine,
it is copy-pasted, never typed). They paste it in Settings → Licence and the app
unlocks for that period. Renewing early: the clinic keeps working until the old
key's date, then the new one takes over — just issue from today; there is no state
on your side to keep beyond this script and the private key.

The date maths clamps the day (e.g. 31 Jan + 1 month → 28/29 Feb), matching the
hosted portal's `_add_months`.
"""
import calendar
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from user_mgmt.licensing import make_token  # noqa: E402


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def main():
    if len(sys.argv) < 3:
        print('Usage: python licensing/sign_license.py "<Clinic Name>" <months>')
        raise SystemExit(2)
    clinic = sys.argv[1]
    months = int(sys.argv[2])

    key_file = Path(__file__).resolve().parent / "private_key.json"
    if not key_file.exists():
        print("private_key.json not found. Run `python licensing/keygen.py` once "
              "to create your signing key (and keep it safe).")
        raise SystemExit(1)
    priv = json.loads(key_file.read_text(encoding="utf-8"))

    today = date.today()
    exp = add_months(today, months)
    token = make_token(clinic, exp, today, priv)

    print(f"\nClinic : {clinic}")
    print(f"Months : {months}")
    print(f"Valid  : {today.isoformat()}  ->  {exp.isoformat()}")
    print("\nLicence key (send this to the clinic):\n")
    print(token)
    print()


if __name__ == "__main__":
    main()
