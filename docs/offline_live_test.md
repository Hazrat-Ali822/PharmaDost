# Deploying to JabraHost and testing the offline system live

Run these in order. Step 0 is the one that silently breaks everything else.

---

## 0. HTTPS must be working (check this first)

Browsers refuse to run a **service worker** on plain `http`. Without it, no page is
cached, so the moment the connection drops the staff get the offline page instead of
the app — and everything below fails for a reason that has nothing to do with the code.

cPanel → **SSL/TLS Status** → run **AutoSSL** for `sehatyar.online`. Then open
`https://sehatyar.online` and confirm the padlock. If the site still loads on `http://`,
add a redirect to `https://`.

## 1. Deploy

```bash
cd ~/sehatyar
git pull

source /home/<cpaneluser>/virtualenv/sehatyar/3.10/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

`collectstatic` is **not optional** — `static/js/offline.js` is the whole offline
client. Skip it and browsers keep running the old file.

Then cPanel → **Setup Python App → Restart**. Nothing takes effect until you do.

## 2. Confirm the deploy landed

Open these in the browser while signed in:

| URL | Expect |
|---|---|
| `/sw.js` | JavaScript, and a long `SHELL = [...]` list of paths |
| `/offline/ping/` | Blank page, status **204** (check the Network tab) |
| `/offline/queue/` | "Offline Queue" screen, empty |
| `/offline/slip/` | A letterhead slip saying "Nothing to print" |

If `/sw.js` shows an old short SHELL, `collectstatic`/restart did not happen.

---

## 3. Live offline test — do this exactly

**Test A — the basic loop**

1. Sign in. Wait ~15 seconds without touching anything (the worker is caching every
   screen in the background; DevTools → Application → Cache Storage fills up).
2. Turn the laptop's **wifi off**.
3. Open **Patients → Add Patient**. *The page must open.* If you get the offline page,
   go back to step 0 — HTTPS or caching is not working.
4. Fill it in, Save. Expect: **"💾 No connection — saved on this device."**
5. Bottom-left shows **"⏳ 1 waiting to sync"**.
6. Turn wifi **on**. Within about a minute (or immediately on the first click):
   **"✅ 1 offline record synced."**
7. Open the patient list — the patient is there, with a real MRN.

**Test B — the one that used to lose data**

This is the case that matters most in KPK: wifi connected, internet dead.

1. Keep wifi **on** but pull the router's internet cable (or turn off mobile data while
   staying on a hotspot with no uplink).
2. Register a patient. It must say **"saved on this device"**, not show a browser error
   page. *(Before this work the entry was simply lost here.)*

**Test C — the offline handoff (reception → doctor)**

1. Wifi off. Reception → **New Visit**, register a patient and pick a doctor, Save.
2. A **provisional slip** opens and the print dialogue appears: patient name, doctor,
   `OFF-1`, and "MRN and token will be issued when it syncs".
3. Print it. That paper is how the doctor learns the patient has arrived — no
   notification can travel between two devices with no server in between.
4. Wifi on → it syncs → the patient now has a real MRN and token.

**Test D — no notification storm**

1. Wifi off. Register **5** visits.
2. Wifi on, wait for the sync.
3. Sign in as the doctor. The bell must show **1** unread ("📥 5 entries made offline …"),
   not 5. The five detail lines are there, marked `⏱ … offline —` and already read.

**Test E — no duplicates**

1. Wifi off, register one patient.
2. Wifi on, let it sync, then press **Sync now** on `/offline/queue/` a few times, and
   reload the page.
3. Exactly **one** patient exists. The ledger answers replays instead of re-applying.

**Test F — a rejection is visible**

1. Wifi off. Book a visit for a doctor, then (as an admin on another machine, online)
   delete that doctor.
2. Wifi on. The entry is **rejected**, and `/offline/queue/` shows it in red with the
   reason and a **Try again** / **Discard** button. It must not sit at "waiting" forever.

---

## If something fails

| Symptom | Cause |
|---|---|
| Offline page instead of the app | No HTTPS, or the worker never installed — step 0 |
| Form submits and shows a browser error offline | Old `offline.js` — `collectstatic` + restart |
| "⏳ waiting" forever, never syncs | Open `/offline/queue/`; if it is red it was rejected, and the reason is printed |
| Everything syncs twice | Should be impossible — check `ClientAction` rows have unique `client_uuid` |
| Bell floods after reconnect | `Notification` created via `bulk_create` somewhere — it skips the `save()` hook |

## What still will not work offline, by design

- **Notifications to another device.** No server between them, no channel. The printed
  slip covers the reception→doctor handoff; for it to appear on the doctor's *screen*,
  run the LAN server (`docs/lan_setup.md`).
- **Another device's offline entries.** Each device holds its own queue until it syncs.
- **MRN, token and invoice numbers at the moment of entry.** They are issued by the
  server at sync, and the slip says so.
- **Chained entries** — registering a patient offline and then opening *that patient's*
  prescription form. The patient does not exist on the server yet. The reception visit
  screen (register + book in one form) is deliberately one form for this reason.
- **Stock accuracy.** Two devices can each sell the last unit offline; the sale is
  recorded, billed in full and flagged `needs_reconcile`, and the pharmacist is told.
