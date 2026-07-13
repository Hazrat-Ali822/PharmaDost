# PharmaDost — Local Desktop App (Windows)

Run PharmaDost as a normal Windows program. **No internet, no Python, no server setup**
on the customer's PC — all their data stays on their own machine.

- Database + uploaded files live in a private per-user folder that survives re-installs:
  `C:\Users\<name>\AppData\Local\PharmaDost\`
- First launch shows the **Setup Wizard** (business name, admin account, which modules to
  enable). Everything is adjustable later from **Settings**.
- Because each PC has its own local database file, three different businesses on three
  different PCs never conflict — there is nothing shared between them.

---

## A) How the customer uses it (after you hand them the app)

1. Unzip `PharmaDost` anywhere (Desktop, `C:\PharmaDost`, a USB stick — anywhere).
2. Double-click **`PharmaDost.exe`**.
3. A small status window opens and the app opens in a window/browser at
   `http://127.0.0.1:...`.
4. First time only: the **Setup Wizard** appears → enter business name, create the admin
   login, tick the modules they want (Pharmacy / OPD / Lab / Imaging / Billing / Reports).
5. Done. Sign in with the admin account and work normally.
6. To stop the app: close the small status window.

**Backup:** Admin Dashboard → **💾 Backup** (or Settings → Backup) downloads a single
`.zip` with the whole database + uploaded files. To restore on a new PC: install the app,
then unzip that file's contents into
`C:\Users\<name>\AppData\Local\PharmaDost\` (replacing `db.sqlite3` and the `media` folder).

---

## B) How YOU build the `.exe` (one time, on your dev PC)

You need the project with its `.venv` already working.

**Easiest — just run the build script from the project root:**

```
desktop\build.bat
```

It installs the build tools, collects static files, and runs PyInstaller. When it
finishes you get:

```
dist\PharmaDost\PharmaDost.exe
```

**Or do it manually:**

```powershell
# from the project root, with the venv active
pip install -r requirements.txt -r requirements-desktop.txt
python manage.py collectstatic --noinput
pyinstaller desktop\PharmaDost.spec --noconfirm
```

### Ship it
Zip the **whole** `dist\PharmaDost\` folder (not just the .exe — it needs the files next to
it) and send that zip to the customer. They unzip and double-click. That's it.

---

## Notes & options

- **Native window vs browser:** if `pywebview` is bundled (it's in
  `requirements-desktop.txt`), the app opens in its own desktop window. If not, it opens in
  the default browser. Both are the same app.
- **Hide the console window:** in `desktop/PharmaDost.spec` set `console=False`. Keep it
  `True` while testing so you can see startup messages.
- **App icon:** drop an `app.ico` file into the `desktop\` folder before building and it's
  used automatically.
- **One-file .exe:** this build makes a *folder* app (faster startup, standard for Django).
  A single-file `.exe` is possible but starts slower because it unpacks on every run.
- **Updating the app:** rebuild and send the new `dist\PharmaDost` folder. Customer data is
  untouched because it lives in the separate AppData folder, not inside the app.
- **This does not affect the web/PythonAnywhere deployment.** The same codebase runs both
  ways; the desktop launcher just points Django at a local data folder and a localhost
  server.
```
