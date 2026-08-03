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
  computer. Use the in-app Backup button, or copy that folder to a USB stick weekly.
  One failed hard disk with no backup is the whole hospital's records.

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
