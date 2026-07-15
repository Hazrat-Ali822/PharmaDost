# Workspace Guidelines: PharmaDost Hospital & Pharmacy Management System

Welcome! This document provides an A-to-Z overview of the PharmaDost codebase, system architecture, database configurations, and established patterns. Please review this before starting any coding tasks.

---

## 1. Project Overview & Tech Stack
PharmaDost is a multi-tenant SaaS Hospital & Pharmacy Management System.
* **Core**: Django 4.2+, Python 3.10+, HTML5, Vanilla JavaScript.
* **Styling**: Modern UI Design system in [app.css](file:///C:/Users/Hazrat%20Ali/Desktop/PharmaDost/static/css/app.css). Uses dynamic variables (`--primary`, `--accent`) for system-wide branding.
* **Server**: Deployed on **PythonAnywhere** (uWSGI + PostgreSQL Supabase database) and packageable as a local desktop app (SQLite).

---

## 2. Architecture & Design Patterns

### A. Multi-Tenant Boundaries
* **Tenant Isolation**: Each hospital is a separate tenant represented by the `Hospital` model in `saas` app.
* **Current Tenant Resolution**: `saas.middleware.TenantMiddleware` resolves the active tenant using `request.user.hospital` and binds it to a thread-local variable via `saas.utils.set_current_hospital()`.
* **Accessing Current Tenant**: Always query the active tenant using `saas.utils.get_current_hospital()`. Do not perform raw tenant filtering manually.

### B. Isolated Branding & Settings
* **Model**: `SiteSettings` in `user_mgmt` holds color schemes, logo image/text, licensing, footers, etc.
* **Multi-Tenant SiteSettings**:
  - `SiteSettings` contains a `OneToOneField` to `saas.Hospital` (null=True).
  - The `load()` classmethod loads the settings for `get_current_hospital()`.
  - **Copy on First-Load Pattern**: If a hospital loads settings for the first time, the `load()` method initializes it dynamically using static `SITE_DEFAULTS` (default blue color) to avoid inter-tenant color pollution.
  - Falls back to `pk=1` (global fallback row) only if no hospital context exists.

### C. Style Customizations
* **Dynamic Styling**: The master layout in [base.html](file:///C:/Users/Hazrat%20Ali/Desktop/PharmaDost/templates/partials/base.html) injects tenant-specific styles:
  ```html
  <link rel="stylesheet" href="{% static 'css/app.css' %}?v=1.6">
  {% if branding.primary_color %}<style>:root{ --primary: {{ branding.primary_color }}; --accent: {{ branding.accent_color }}; }</style>{% endif %}
  ```
* **Busting Browser Cache**: When updating global styling or assets in `app.css`, always increment the version query string (`?v=X.X`) across all templates containing stylesheet links.

---

## 3. Core Pipelines

### A. Doctor-Prescription-to-Pharmacy Pipeline
1. **Prescription Status**: EMR doctor prescriptions have a `status` field (`'PENDING'`, `'DISPENSED'`).
2. **Intake Flow**: Doctors write prescriptions using the EMR form. If a medicine is not found in the catalog, they type it into the nullable/custom fields.
3. **Notification Badges**: Sidebar contains a dynamic red counter next to "Bills" highlighting the count of `'PENDING'` prescriptions for the current hospital. Calculated in `accounts.context_processors.nav_permissions`.
4. **Editable POS Pre-Population**: Visiting POS with URL query parameter `?prescription_id=x` pre-loads patient details and auto-populates the cart rows. Warns the pharmacist about low stock or items not in catalog, letting them edit or skip them. On POS sale submission, the prescription status is updated to `'DISPENSED'`.

---

## 4. Production Deployment Guidelines (PythonAnywhere)
Every time you push changes affecting static files (CSS/JS) or database models:
1. **Pull Code**: `git pull`
2. **Migrate Database**: Ensure virtualenv is active, load environment variables using `.env` or explicit loaders:
   ```bash
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pharma_mgmt.settings'); import django; django.setup(); from django.core.management import call_command; call_command('migrate')"
   ```
3. **Collect Static Files**: Compile static assets:
   ```bash
   python manage.py collectstatic --noinput
   ```
4. **Reload Web App**: You **MUST** log in to the PythonAnywhere dashboard, go to the **Web** tab, and click the green **Reload** button to restart the WSGI daemon.
