from django.test import TestCase, Client

from accounts.models import User
from user_mgmt.models import SiteSettings
from user_mgmt.middleware import SetupMiddleware


class SetupWizardTest(TestCase):
    def setUp(self):
        SetupMiddleware._configured = False  # fresh install state

    def test_fresh_install_forces_setup(self):
        c = Client(SERVER_NAME='127.0.0.1')
        r = c.get('/patients/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers['Location'], '/setup/')
        self.assertEqual(c.get('/setup/').status_code, 200)

    def test_setup_creates_admin_and_modules(self):
        c = Client(SERVER_NAME='127.0.0.1')
        r = c.post('/setup/', {
            'brand_name': 'Test Clinic', 'email': 'boss@test.com',
            'password': 'pharma123', 'modules': ['pharmacy', 'finance']})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(email='boss@test.com')
        self.assertTrue(u.is_superuser and u.role == 'ADMIN')
        self.assertEqual(set(SiteSettings.load().enabled_modules), {'pharmacy', 'finance'})

    def test_setup_blocked_once_configured(self):
        User.objects.create_superuser(email='x@y.com', password='pharma123')
        SetupMiddleware._configured = True
        c = Client(SERVER_NAME='127.0.0.1')
        r = c.get('/setup/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r.headers['Location'])
