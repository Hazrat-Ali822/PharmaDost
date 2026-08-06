# Clinic LAN setup — run Sehatyar with no internet

For a clinic where the internet is down most of the day (KPK and anywhere like it).
One computer in the clinic becomes the server; every phone and computer on the same
wifi uses it. **Nothing depends on the internet** — notifications, tokens, stock, the
doctor↔reception handoff, all of it works exactly as it does on the hosted site,
because the server is in the room.

---

## What you need

- One Windows computer that stays on during clinic hours (any laptop is fine)
- A wifi router — **the internet does not have to work**, the router only has to be on
- Every phone/computer connected to that same wifi

## Setting it up (once)

**1. Build or copy the desktop app**

```bat
desktop\build.bat
```

This produces `dist\PharmaDost\PharmaDost.exe`. Copy that whole `PharmaDost` folder
to the clinic computer.

**2. First run — as administrator**

Right-click `PharmaDost.exe` → **Run as administrator**, just this once. The app adds
a Windows Firewall rule for its port; without it Windows silently drops every
connection from the phones and there is no error message anywhere to explain why.

After that first run, normal double-click is fine.

**3. Read the address off the startup window**

```
==================================================================
  Sehatyar is running
==================================================================
  On this computer : http://127.0.0.1:8000/
  On phones/tablets: http://192.168.1.7:8000/
  Firewall         : allowed on port 8000
```

**4. Connect the phones**

In the app on the clinic computer: sidebar → **App → Connect a Device**. It shows a
QR code and the address. On each phone: connect to the same wifi, scan the QR (or type
the address in Chrome), sign in with that person's own staff account, then
"Add to Home screen" so nobody types it again.

Print that page and stick it on the wall — it is the one thing new staff need.

---

## What works, and what to know

**Works with no internet at all:** every screen, every form, notifications, tokens,
MRNs, stock deduction, billing, the doctor↔reception handoff. All devices see the same
data live, because they share one database on the clinic computer.

Things worth knowing before you rely on it:

- **The clinic computer must stay on and awake.** Closing the app stops it for
  everyone. Set the power plan to never sleep.
- **The address can change.** The router hands out addresses, and after a reboot this
  computer may get a different one. If phones stop connecting, open **Connect a Device**
  again and check the number. To stop this for good, set a *DHCP reservation* (also
  called "static lease") for the clinic computer in the router's admin page.
- **On the wifi the app runs over plain `http`,** so phones cannot "install" it as an
  app or cache pages — they do not need to, the server is right there. If a phone
  briefly drops off the wifi, whatever was typed is still queued in its outbox and
  syncs when it rejoins (sidebar → **Offline Queue**).
- **It is not on the internet.** Only devices on your wifi can reach it. That is a
  security feature, not a limitation.
- **Back it up.** Everything lives in `%LOCALAPPDATA%\PharmaDost` on the clinic
  computer. The app **backs itself up automatically on every launch** — a snapshot
  (database + uploaded files) is zipped into `%LOCALAPPDATA%\PharmaDost\backups`,
  keeping the last 14. A computer started each morning is therefore backed up daily
  with no one having to remember.

  That local copy does **not** survive the computer being **stolen or its disk
  dying** — for that you need an **off-machine** copy. Set one folder and it is
  written there too, every launch:

  ```bat
  set PHARMADOST_BACKUP_DIR=E:\SehatyarBackups     REM a USB stick that stays plugged in
  PharmaDost.exe
  ```

  Point it at a **USB stick, a second drive, or a cloud-synced folder** (OneDrive /
  Google Drive — those upload the backup whenever the internet is up). Keep the USB
  somewhere other than on top of the computer, so one theft does not take both. The
  in-app **Backup** button still works for an on-demand copy.

## Cloud backup to the provider, and restore after a loss

Beyond the local/USB backups above, a **licensed** install also sends a copy of its data
to the hosted site whenever it has internet — so the provider always holds a recent
snapshot, even if the clinic never sets up a USB.

- **Automatic, in the background.** On every launch, once the app is licensed and **only
  when the data has changed since last time**, it uploads a fresh copy to
  `https://sehatyar.online` (override with `PHARMADOST_CLOUD_URL`, or set it empty to turn
  cloud backup off). No internet → it just tries again next time. An idle day sends nothing.
- **One file per install.** Each upload replaces the previous one, so the provider always
  holds exactly one current copy per clinic — the host does not fill up.
- **Authenticated by the licence key** — no extra password to set up. The provider sees
  each install's copy at **Owner Portal → Desktop Backups**.

**Restoring after a lost / stolen / dead computer:**

1. The provider downloads the clinic's latest backup from **Desktop Backups** and sends
   the `.zip` file to them.
2. On the new computer, install and open the app, then go to **Restore Data** (sidebar) →
   choose that `.zip` → **Restore**.
3. The app asks them to **close and reopen** it — the data is put back on restart, and the
   install is exactly as it was when that backup was taken.

> The clinic's own automatic backups (in `%LOCALAPPDATA%\PharmaDost\backups` or the USB
> folder) restore the same way — Restore Data accepts any of them.

**This is backup and restore, not live sync.** Entering a patient on the LAN server does
not put them on the hosted website; the uploaded file is stored as-is for safekeeping and
handed back on request.

## Monthly subscription (offline licence)

The desktop / LAN build enforces its monthly subscription **on the device**, with no
internet — through a signed **licence key**. (The hosted site does this differently,
through the online SaaS portal.)

**How it works**

- A fresh install runs on a **14-day free trial** so the clinic can start at once.
- Before the trial ends, the clinic pastes a **licence key** in the app: sidebar →
  **Licence** (or Settings → Licence), which unlocks it for the paid period.
- When the licence (or the trial) lapses, **every screen locks** — on the server
  computer *and* on every phone on the wifi, because they all go through this one
  server — until a fresh key is entered. A warning banner shows for the last 5 days.

**Issuing keys (provider side)** — you keep the signing key; nobody else can make a
valid licence, even though the code is open:

```bat
REM once, ever — creates your private signing key (keep it safe, never share it):
python licensing\keygen.py
REM   (paste the printed PUBLIC_KEY into user_mgmt/licensing.py, commit that)

REM then each month, per clinic — prints a key to send them (WhatsApp is fine):
python licensing\sign_license.py "Shaheen Clinic" 1
python licensing\sign_license.py "Al-Shifa Hospital" 12    REM a full year
```

The clinic copy-pastes that key into **Licence**. It is verified on the device, so
activation needs no internet. Renewing early is fine — issue a new key any time; the
clinic keeps working until the old date, then the new one takes over.

> **Keep `licensing/private_key.json` secret and off the internet.** It is the one
> thing that lets a licence be signed; it is git-ignored so it is never committed.
> Losing it means generating a new keypair (and re-issuing everyone's keys).

## Turning LAN mode off

If a machine should be single-user only:

```bat
set PHARMADOST_LAN=0
PharmaDost.exe
```

## Changing the port

The port is fixed at 8000 so a bookmarked address keeps working. If something else on
the machine already uses 8000, the app moves to 8080, 8800 or 5000 and prints which.
To choose it yourself:

```bat
set PHARMADOST_PORT=9000
PharmaDost.exe
```

---

## Troubleshooting

| What you see | What it is |
|---|---|
| Phone: page does not load at all | Different wifi, or the firewall rule was never added — re-run once as administrator |
| Phone: "Bad Request (400)" | The address is one the app did not detect. Open **Connect a Device** and use the address shown there |
| Was working, now nothing connects | The router gave the computer a new address. Check **Connect a Device**; set a DHCP reservation to stop it happening again |
| Phone shows an old page | Pull down to refresh. Phones on the LAN do not cache, so this is rare |
| Two computers both running the app | **Do not.** Each has its own database and they will not merge. One server only |

## The one thing to decide first

The clinic LAN server and the hosted site at `sehatyar.online` are **two separate
databases**. There is no two-way sync between them today — entering a patient on the
LAN server does not put them on the hosted site, and the reverse is also true.

Pick one as the real system:

- **LAN server** — right for a clinic with unreliable internet. The hosted site then
  serves only as a demo or a future migration target.
- **Hosted site** — right where the internet is mostly up. The offline outbox already
  covers the gaps: staff keep working through an outage and everything syncs after.

Running both as live systems at once will produce two divergent sets of records.
