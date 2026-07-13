# PharmaDost — Distribute via Download Link

You picked: **Local Windows app (.exe) shared with a download link.** No Python install
for the customer, each PC keeps its own data, works offline.

The ready-to-ship file is: **`PharmaDost-App.zip`** (~26 MB) in the project folder.

---

## 1) Put it online (one time, or whenever you rebuild)

Upload **`PharmaDost-App.zip`** somewhere that gives a shareable link:

- **Google Drive** (easiest): drive.google.com → New → File upload → pick
  `PharmaDost-App.zip` → right-click the file → **Share** → "Anyone with the link" →
  **Copy link**. Give that link to the customer.
- Or **Dropbox / OneDrive / your website** — same idea, get a public download link.

> Tip: Drive may warn "can't scan for viruses (too big)" — that's normal, the user just
> clicks **Download anyway**.

---

## 2) What the customer does (send them these 4 steps)

1. Open the link → **Download** → you get `PharmaDost-App.zip`.
2. **Right-click the zip → Extract All** (unzip it). ⚠️ Must extract — don't run from
   inside the zip.
3. Open the extracted **PharmaDost** folder → double-click **`PharmaDost.exe`**.
   - First time, Windows SmartScreen may say "Windows protected your PC" → click
     **More info → Run anyway** (this is normal for an unsigned app).
4. The app opens. **First run shows the Setup Wizard** → enter business name, create the
   admin login, tick the modules they want → done. From then on they just double-click
   the .exe.

**Their data** lives at `C:\Users\<name>\AppData\Local\PharmaDost\` — separate on every
PC, safe across updates. Backup any time from **Admin → 💾 Backup**.

---

## 3) When you add features later (rebuild + re-share)

```
desktop\build.bat
```
then re-zip and re-upload:
```powershell
Compress-Archive -Path 'dist\PharmaDost\*' -DestinationPath 'PharmaDost-App.zip' -Force
```
Send the new link. **Customer data is NOT touched** — it lives in the separate AppData
folder, so they just replace the app folder and keep working.

---

## Notes
- The shipped app starts **clean** (no demo data) — the Setup Wizard configures each
  install. The `seed_demo` data was only for your own testing.
- No internet needed to run. No Python needed on the customer's PC (it's bundled).
- To hide the little black console window: in `desktop/PharmaDost.spec` set
  `console=False`, then rebuild.
- App icon: drop `desktop/app.ico` before building.
