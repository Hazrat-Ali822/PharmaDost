# Deploying PharmaDost to PythonAnywhere (free tier)

Your username: **PharmaDost** → your site will be **https://pharmadost.pythonanywhere.com**

Everything below is copy-paste ready. Do the steps **in order**.

---

## 0. What you already have
- A clean upload file on your Desktop: **`pharmadost_deploy.zip`** (source only — no venv/db/media; those get regenerated on the server).
- A ready-to-use production secret key (used in step 3):
  ```
  ob1bax)==ylw8+f4lbh!eyhk33@hw$w(#9kc13j&qjrx^sq=2^
  ```

---

## 1. Upload the code
1. On PythonAnywhere, go to the **Files** tab.
2. Under “Files”, click **Upload a file** and upload `pharmadost_deploy.zip` (it lands in `/home/PharmaDost/`).

## 2. Unzip + create the virtualenv + install
1. Go to the **Consoles** tab → start a **Bash** console. Run:
   ```bash
   cd ~
   unzip pharmadost_deploy.zip          # creates /home/PharmaDost/PharmaDost/
   cd PharmaDost
   ls                                   # you should see manage.py, pharma_mgmt/, etc.
   ```
2. Create a Python 3.10 virtualenv and install the requirements:
   ```bash
   mkvirtualenv --python=/usr/bin/python3.10 pharmadost
   pip install -r requirements.txt
   ```
   (The prompt now shows `(pharmadost)`. Next time, re-enter it with `workon pharmadost`.)

## 3. Create the `.env` file (production settings)
Still in the Bash console, inside `/home/PharmaDost/PharmaDost`:
```bash
cat > .env << 'EOF'
DJANGO_ENV=production
DJANGO_SECRET_KEY=ob1bax)==ylw8+f4lbh!eyhk33@hw$w(#9kc13j&qjrx^sq=2^
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=pharmadost.pythonanywhere.com
DJANGO_CSRF_TRUSTED=https://pharmadost.pythonanywhere.com
EOF
```

## 4. Set up the database, demo data & static files
```bash
python manage.py migrate
python manage.py seed_demo          # creates the 8 role users + demo data + images
python manage.py collectstatic --noinput
```
(Optional — a real Django /admin superuser:)
```bash
python manage.py createsuperuser
```

## 5. Create the Web app
1. Go to the **Web** tab → **Add a new web app** → **Next**.
2. Choose **Manual configuration** (NOT “Django”) → **Python 3.10** → **Next**.

## 6. Point the Web app at the project
On the **Web** tab, set these fields:

- **Source code:** `/home/PharmaDost/PharmaDost`
- **Working directory:** `/home/PharmaDost/PharmaDost`
- **Virtualenv:** `/home/PharmaDost/.virtualenvs/pharmadost`

## 7. Edit the WSGI file
On the **Web** tab, click the **WSGI configuration file** link (opens an editor).
**Delete everything** in it and paste exactly this:
```python
import os, sys

path = '/home/PharmaDost/PharmaDost'
if path not in sys.path:
    sys.path.insert(0, path)

os.environ['DJANGO_SETTINGS_MODULE'] = 'pharma_mgmt.settings'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```
Click **Save**.

## 8. Static & media file mappings
On the **Web** tab, scroll to **Static files** and add two rows:

| URL        | Directory                                          |
|------------|----------------------------------------------------|
| `/static/` | `/home/PharmaDost/PharmaDost/staticfiles`          |
| `/media/`  | `/home/PharmaDost/PharmaDost/media`                |

(These make the CSS, medicine images, logos and scan images load.)

## 9. Reload & open
1. Click the big green **Reload pharmadost.pythonanywhere.com** button.
2. Open **https://pharmadost.pythonanywhere.com** 🎉

---

## Login details (share with your testers)
All demo users — password **`pharma123`**:

| Role         | Email                        |
|--------------|------------------------------|
| Admin        | admin@pharmadost.com         |
| Receptionist | reception@pharmadost.com     |
| Doctor       | doctor@pharmadost.com        |
| Pharmacist   | pharmacist@pharmadost.com    |
| Wholesale    | wholesale@pharmadost.com     |
| Lab Tech     | labtech@pharmadost.com       |
| Sonographer  | sonographer@pharmadost.com   |
| Accountant   | accountant@pharmadost.com    |

---

## Troubleshooting
- **“Something went wrong :-(” / 502:** open the **Error log** (Web tab) — it names the exact problem. 95% of the time it’s a wrong path in the WSGI file or the virtualenv field.
- **CSS/images missing:** re-check the static/media mappings (step 8), then **Reload**. Make sure you ran `collectstatic`.
- **CSRF verification failed on login/forms:** confirm `.env` has `DJANGO_CSRF_TRUSTED=https://pharmadost.pythonanywhere.com`, then Reload.
- **DisallowedHost:** confirm `.env` `DJANGO_ALLOWED_HOSTS=pharmadost.pythonanywhere.com`, then Reload.
- **After changing code or `.env`:** always click **Reload** on the Web tab.
- **Free tier:** the app pauses after ~3 months — just log in and click the “Run until 3 months from today” button on the Web tab to keep it alive.

## Updating the app later
1. Upload a new `pharmadost_deploy.zip`, then in a Bash console:
   ```bash
   workon pharmadost
   cd ~/PharmaDost
   # unzip over the top (keeps your .env, db.sqlite3 and media):
   unzip -o ~/pharmadost_deploy.zip -d ~
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```
2. **Reload** on the Web tab.

> ⚠️ Note: SQLite is great for testing with a handful of users. For a real live clinic (many people saving at once), move to Postgres (Supabase) later.
