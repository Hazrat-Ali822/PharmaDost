"""Load / performance testing with Locust.

Answers the question a buyer will ask: how many staff can use this at once before
it gets slow? It drives the real screens a hospital hammers all day — the POS, the
medicine list, patient search, dashboards and reports.

Run against a DISPOSABLE instance, never production — the pharmacist task posts
real sales.

    pip install -r requirements-dev.txt
    python manage.py runserver 8000            # in another terminal

    # web UI at http://localhost:8089
    locust -f loadtest/locustfile.py --host http://localhost:8000

    # or headless: 50 users, ramping 5/second, for 2 minutes
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
           --headless -u 50 -r 5 -t 2m --csv loadtest/results

Seed logins first (`python manage.py seed_demo`) or set LOAD_EMAIL / LOAD_PASSWORD:

    LOAD_EMAIL=admin@demo.com LOAD_PASSWORD=pharma123 locust -f loadtest/locustfile.py ...

Reading the output: `p95` is the number that matters — the slowest 5% of requests.
A page over ~1s at your expected concurrency is worth investigating, and any
non-zero failure count means requests are erroring, not just running slow.
"""
import os
import random
import re

from locust import HttpUser, task, between

EMAIL = os.getenv('LOAD_EMAIL', 'admin@demo.com')
PASSWORD = os.getenv('LOAD_PASSWORD', 'pharma123')

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


class PharmaDostUser(HttpUser):
    """Base: logs in once per simulated user, then browses."""

    abstract = True
    wait_time = between(1, 4)          # think time between actions

    def on_start(self):
        self.login()

    def login(self):
        resp = self.client.get('/login/', name='/login/ [GET]')
        match = CSRF_RE.search(resp.text)
        if not match:
            resp.failure('no CSRF token on the login page')
            return
        self.client.post(
            '/login/',
            {'username': EMAIL, 'password': PASSWORD,
             'csrfmiddlewaretoken': match.group(1)},
            headers={'Referer': f'{self.host}/login/'},
            name='/login/ [POST]',
        )

    def _get(self, path, name=None):
        with self.client.get(path, name=name or path, catch_response=True) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code in (301, 302):
                r.failure('redirected — the session probably expired')
            else:
                r.failure(f'HTTP {r.status_code}')


class CounterStaff(PharmaDostUser):
    """A pharmacy counter: the busiest, most latency-sensitive user."""

    weight = 5

    @task(10)
    def open_pos(self):
        self._get('/sales/new/', name='POS')

    @task(6)
    def browse_medicines(self):
        self._get('/medicines/', name='Medicine list')

    @task(4)
    def search_medicine(self):
        term = random.choice(['par', 'amox', 'brufen', 'syp', 'tab'])
        self._get(f'/medicines/?q={term}', name='Medicine search')

    @task(2)
    def bill_history(self):
        self._get('/sales/list/', name='Bills')

    @task(1)
    def dashboard(self):
        self._get('/', name='Dashboard')


class ReceptionStaff(PharmaDostUser):
    """Front desk: patient lookup and appointments."""

    weight = 3

    @task(8)
    def patient_list(self):
        self._get('/patients/', name='Patient list')

    @task(5)
    def patient_search(self):
        term = random.choice(['ali', 'kh', 'muh', 'fat', 'a'])
        self._get(f'/patients/?q={term}', name='Patient search')

    @task(4)
    def appointments(self):
        self._get('/opd/appointments/', name='Appointments')

    @task(2)
    def invoices(self):
        self._get('/billing/', name='Invoices')


class ManagerUser(PharmaDostUser):
    """Reports aggregate a lot of rows — the usual first thing to get slow."""

    weight = 1

    @task(4)
    def sales_report(self):
        self._get('/reports/sales/', name='Sales report')

    @task(3)
    def inventory_report(self):
        self._get('/reports/inventory/', name='Inventory report')

    @task(2)
    def analytics(self):
        self._get('/medicines/analytics/', name='Inventory analytics')

    @task(2)
    def visual_analytics(self):
        self._get('/reports/analytics/', name='Visual analytics')

    @task(1)
    def expiry_and_reorder(self):
        self._get('/medicines/expiry/', name='Expiry report')
        self._get('/medicines/reorder/', name='Reorder report')
