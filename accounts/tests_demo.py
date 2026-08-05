"""One-click /demo/ login and the login-page 'Try the live demo' button."""
from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from saas.models import Hospital


class DemoLoginTest(TestCase):
    def test_login_page_shows_the_demo_button(self):
        page = Client().get('/login/')
        self.assertContains(page, 'Try the live demo')
        self.assertContains(page, '/demo/')

    def test_demo_route_without_a_demo_user_redirects_to_login(self):
        resp = Client().get('/demo/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    def test_demo_route_signs_the_visitor_in(self):
        h = Hospital.objects.create(name='Demo Hospital', slug='demo-hospital',
                                    expiry_date=date.today() + timedelta(days=3650))
        User.objects.create_user(email='demo@sehatyar.online', password='demo1122',
                                 role='ADMIN', hospital=h)
        c = Client()
        resp = c.get('/demo/')
        self.assertEqual(resp.status_code, 302)
        # now authenticated — a protected page no longer bounces to login
        self.assertTrue('_auth_user_id' in c.session)
