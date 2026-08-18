"""Switching demo role without a password, and never outside the demo.

`/demo/` has always signed any visitor in as the demo **admin** with no password
— the most privileged of that tenant's eight accounts — so admitting its doctor
or its nurse the same way widens nothing. What has to hold is the boundary: this
must never reach a real hospital's account, and the switcher must never appear
for a real tenant.

The reason it exists is worth keeping in view: the admin's screen is the least
representative of the eight, and a password box is a wall for anyone driving the
demo without a keyboard — a browser agent included.

    python manage.py test tests.test_demo_roles --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.core.cache import cache
from django.test import Client, TestCase

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _future():
    return date.today() + timedelta(days=3650)


class DemoRoleSwitchTest(TestCase):

    def setUp(self):
        cache.clear()                       # the demo hospital id is cached
        self.demo = Hospital.objects.create(name='Sehatyar Demo Hospital',
                                            slug='demo', expiry_date=_future())
        self.real = Hospital.objects.create(name='Real Hospital', slug='real-h',
                                            expiry_date=_future())
        self.admin = User.objects.create_user(
            email='demo@sehatyar.online', password='x', role='ADMIN',
            hospital=self.demo, first_name='Demo', last_name='Admin')
        self.doctor = User.objects.create_user(
            email='demo.doctor@sehatyar.online', password='x', role='DOCTOR',
            hospital=self.demo, first_name='Imran', last_name='Khan')
        self.nurse = User.objects.create_user(
            email='demo.nurse@sehatyar.online', password='x', role='NURSE',
            hospital=self.demo)
        # A real tenant's doctor, who must never be reachable this way.
        self.outsider = User.objects.create_user(
            email='doctor@real.com', password='x', role='DOCTOR',
            hospital=self.real)

    def tearDown(self):
        clear_current_hospital()
        cache.clear()

    def test_a_visitor_can_become_the_demo_doctor_with_no_password(self):
        c = Client()
        c.get('/demo/')                                  # in as the admin
        r = c.get('/demo/as/doctor/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(int(c.session['_auth_user_id']), self.doctor.pk)

    def test_it_works_from_a_standing_start_too(self):
        """No prior session — the whole point is that nothing has to be typed."""
        c = Client()
        c.get('/demo/as/nurse/', follow=True)
        self.assertEqual(int(c.session['_auth_user_id']), self.nurse.pk)

    def test_it_never_reaches_a_real_hospitals_account(self):
        """The demo has no ACCOUNTANT here, but a real tenant does have a
        DOCTOR — the query must be scoped by hospital, not by role name."""
        c = Client()
        c.get('/demo/as/doctor/', follow=True)
        self.assertEqual(int(c.session['_auth_user_id']), self.doctor.pk)
        self.assertNotEqual(int(c.session['_auth_user_id']), self.outsider.pk)

    def test_a_role_the_demo_does_not_have_says_so_and_falls_back(self):
        """It lands on the ordinary demo rather than an error page — the visitor
        still gets to see the product — but the message says what happened, so a
        half-seeded demo is visible rather than silently showing the admin."""
        c = Client()
        r = c.get('/demo/as/accountant/', follow=True)
        self.assertIn('not set up', r.content.decode())
        self.assertEqual(int(c.session['_auth_user_id']), self.admin.pk)

    def test_a_nonsense_role_is_refused_rather_than_erroring(self):
        c = Client()
        r = c.get('/demo/as/superuser/', follow=True)
        self.assertEqual(r.status_code, 200)
        # the fallback, never an escalation
        self.assertEqual(int(c.session['_auth_user_id']), self.admin.pk)
        self.assertFalse(User.objects.get(pk=c.session['_auth_user_id']).is_superuser)

    def test_a_superuser_account_in_the_demo_is_not_offered(self):
        """Belt and braces: the demo should hold no superuser, and if one ever
        appears it must not become a passwordless door into the platform."""
        User.objects.create_superuser(email='root@demo.com', password='x')
        User.objects.filter(email='root@demo.com').update(
            hospital=self.demo, role='ADMIN')
        c = Client()
        c.get('/demo/as/admin/', follow=True)
        self.assertEqual(int(c.session['_auth_user_id']), self.admin.pk)

    def test_the_demo_login_route_still_works(self):
        """/demo/as/<role>/ must not swallow /demo/login/, which is the demo
        tenant's own branded sign-in page via the <hospital_slug> route."""
        self.assertEqual(Client().get('/demo/login/').status_code, 200)


class DemoSwitcherIsOnlyInTheDemoTest(TestCase):

    def setUp(self):
        cache.clear()
        self.demo = Hospital.objects.create(name='Sehatyar Demo Hospital',
                                            slug='demo', expiry_date=_future())
        self.real = Hospital.objects.create(name='Real Hospital', slug='real-h',
                                            expiry_date=_future())
        User.objects.create_user(email='demo@sehatyar.online', password='x',
                                 role='ADMIN', hospital=self.demo)
        User.objects.create_user(email='demo.doctor@sehatyar.online', password='x',
                                 role='DOCTOR', hospital=self.demo)
        User.objects.create_user(email='boss@real.com', password='pw',
                                 role='ADMIN', hospital=self.real)

    def tearDown(self):
        clear_current_hospital()
        cache.clear()

    def test_the_strip_is_shown_inside_the_demo(self):
        c = Client()
        c.get('/demo/')
        html = c.get('/dashboard/', follow=True).content.decode()
        self.assertIn('demo-switch', html)
        self.assertIn('/demo/as/doctor/', html)

    def test_it_is_not_shown_to_a_real_tenant(self):
        c = Client()
        c.login(email='boss@real.com', password='pw')
        html = c.get('/dashboard/', follow=True).content.decode()
        self.assertNotIn('demo-switch', html)
        self.assertNotIn('/demo/as/', html)

    def test_a_real_tenant_pays_no_query_for_it(self):
        """It runs on every render, so it must cost a real hospital nothing
        beyond one cached id lookup and an integer comparison."""
        from accounts.context_processors import demo_roles

        c = Client()
        c.login(email='boss@real.com', password='pw')
        c.get('/dashboard/', follow=True)          # warm the cached demo id

        request = type('R', (), {'user': User.objects.get(email='boss@real.com')})()
        with self.assertNumQueries(0):
            self.assertEqual(demo_roles(request), {})
