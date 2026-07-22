"""Performance regression tests: query-count ceilings on the hot paths.

Wall-clock timings are useless in CI, but query counts are stable and catch the
bug that actually makes this app slow on a small host — a query inside a loop.
The dashboard's 7-day chart once ran 21 queries to draw one small graph.

Ceilings are upper bounds with headroom, not exact counts; they should only need
raising when a page genuinely gains a feature. If one of these fails, look for a
`.filter()` or `.count()` inside a `for` loop before you raise the number.

    python manage.py test tests.test_performance --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from billing.models import Expense
from inventory.models import Medicine
from patients.models import Patient
from saas.models import Hospital
from sales.models import Sale


def _future():
    return date.today() + timedelta(days=365)


class QueryBudgetTest(TestCase):
    """Each page must stay under its query budget with a realistic amount of data."""

    @classmethod
    def setUpTestData(cls):
        cls.hospital = Hospital.objects.create(name='Perf', slug='perf',
                                               expiry_date=_future())
        cls.admin = User.objects.create_user(email='perf@t.com', password='pw',
                                             role='ADMIN', hospital=cls.hospital)
        # Enough rows that an N+1 shows up as a count, not a rounding error.
        for i in range(25):
            Medicine.objects.create(name=f'Med {i}', brand='B', price=Decimal('10'),
                                    expiry_date=_future(), hospital=cls.hospital)
            Patient.objects.create(full_name=f'Patient {i}', gender='M',
                                   mrn=f'PERF-{i}', hospital=cls.hospital)
            Sale.objects.create(cashier=cls.admin, hospital=cls.hospital,
                                sale_type='RETAIL', payment_method='CASH',
                                total=Decimal('100'), paid=Decimal('100'))
            Expense.objects.create(description=f'Exp {i}', category='OTHER',
                                   amount=Decimal('10'), date=date.today(),
                                   hospital=cls.hospital, recorded_by=cls.admin)

    def setUp(self):
        cache.clear()          # badge counts are cached; measure the cold path
        self.client = Client()
        self.client.force_login(self.admin)

    def assertQueryBudget(self, url_name, budget):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse(url_name))
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(
            len(ctx), budget,
            f"{url_name} used {len(ctx)} queries (budget {budget}). "
            f"Look for a query inside a loop before raising this."
        )

    # Budgets are for the COLD path (badge cache cleared in setUp), which is why
    # they sit ~10 above the warm numbers a real user sees while browsing.
    def test_dashboard_query_budget(self):
        """Heaviest page: revenue aggregates plus the 7-day chart."""
        self.assertQueryBudget('dashboard', 40)

    def test_medicine_list_query_budget(self):
        self.assertQueryBudget('medicine_list', 20)

    def test_patient_list_query_budget(self):
        self.assertQueryBudget('patient_list', 20)

    def test_pos_query_budget(self):
        self.assertQueryBudget('sale_create', 26)

    def test_sale_list_query_budget(self):
        self.assertQueryBudget('sale_list', 26)


class NotificationPollBudgetTest(TestCase):
    """Every open browser hits this on a timer, so it gets the tightest budget.

    It must NOT build a RequestContext — doing so runs every context processor,
    including the eight sidebar badge counts, on each poll.
    """

    @classmethod
    def setUpTestData(cls):
        cls.hospital = Hospital.objects.create(name='Perf', slug='perf',
                                               expiry_date=_future())
        cls.admin = User.objects.create_user(email='poll@t.com', password='pw',
                                             role='ADMIN', hospital=cls.hospital)

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.force_login(self.admin)

    def test_poll_is_cheap(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('accounts:get_notifications_latest'))
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(
            len(ctx), 6,
            f"notification poll used {len(ctx)} queries. It must not render with "
            f"`request=request`, which pulls in every context processor."
        )


class BadgeScopingTest(TestCase):
    """Badge counts are only computed for modules the user can actually see."""

    @classmethod
    def setUpTestData(cls):
        cls.hospital = Hospital.objects.create(name='Perf', slug='perf',
                                               expiry_date=_future())
        cls.pharmacist = User.objects.create_user(email='ph@t.com', password='pw',
                                                  role='PHARMACIST',
                                                  hospital=cls.hospital)
        cls.admin = User.objects.create_user(email='ad@t.com', password='pw',
                                             role='ADMIN', hospital=cls.hospital)

    def setUp(self):
        cache.clear()

    def _queries_for(self, user):
        c = Client()
        c.force_login(user)
        with CaptureQueriesContext(connection) as ctx:
            c.get(reverse('medicine_list'))
        return len(ctx)

    def test_pharmacist_pays_for_fewer_badges_than_an_admin(self):
        pharmacist_queries = self._queries_for(self.pharmacist)
        cache.clear()
        admin_queries = self._queries_for(self.admin)
        self.assertLess(
            pharmacist_queries, admin_queries,
            "a pharmacist should not pay for the OPD/lab/imaging/IPD/OT counts"
        )

    def test_badges_are_cached_between_requests(self):
        c = Client()
        c.force_login(self.admin)
        c.get(reverse('medicine_list'))                 # warms the badge cache
        with CaptureQueriesContext(connection) as ctx:
            c.get(reverse('patient_list'))
        self.assertLessEqual(
            len(ctx), 10,
            "the second page should reuse the cached badge counts"
        )
