"""In-app user management: creating staff, and the custom-access editor.

An admin adding a user is the moment permissions are decided, and new staff must
land in the admin's own hospital — never unassigned (which used to mean they could
see across tenants) and never in someone else's.

    python manage.py test user_mgmt.tests --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from accounts.permissions import default_features_for_role
from saas.models import Hospital
from saas.utils import clear_current_hospital


def _future():
    return date.today() + timedelta(days=365)


class UserCreationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.admin = User.objects.create_user(email='admin@h1.com', password='pw',
                                             role='ADMIN', hospital=cls.h1)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def _post(self, **overrides):
        data = {
            'email': 'new@h1.com', 'first_name': 'New', 'last_name': 'Staff',
            'role': 'PHARMACIST', 'is_active': 'on', 'password': 'pw12345',
        }
        data.update(overrides)
        return self.client.post(reverse('user_mgmt:user_create'), data)

    def test_new_user_lands_in_the_creating_admins_hospital(self):
        self._post()
        user = User.objects.get(email='new@h1.com')
        self.assertEqual(user.hospital, self.h1)
        self.assertEqual(user.role, 'PHARMACIST')
        self.assertTrue(user.check_password('pw12345'))

    def test_password_is_required_for_a_new_user(self):
        resp = self._post(password='')
        self.assertEqual(resp.status_code, 200)          # form re-rendered
        self.assertFalse(User.objects.filter(email='new@h1.com').exists())

    def test_without_customize_the_user_inherits_role_defaults(self):
        self._post()
        user = User.objects.get(email='new@h1.com')
        self.assertIsNone(user.custom_features)
        self.assertEqual(user.effective_features(),
                         default_features_for_role('PHARMACIST'))

    def test_customize_stores_exactly_the_ticked_features(self):
        self._post(customize='on', features=['pos', 'reports'])
        user = User.objects.get(email='new@h1.com')
        self.assertEqual(set(user.custom_features), {'pos', 'reports'})
        self.assertFalse(user.has_feature('inventory'))  # role default revoked

    def test_bogus_feature_keys_are_dropped(self):
        self._post(customize='on', features=['pos', 'made-up-feature'])
        user = User.objects.get(email='new@h1.com')
        self.assertEqual(user.custom_features, ['pos'])


class UserListScopingTest(TestCase):
    """An admin manages their own staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.admin1 = User.objects.create_user(email='admin@h1.com', password='pw',
                                              role='ADMIN', hospital=cls.h1)
        User.objects.create_user(email='staff@h1.com', password='pw',
                                 role='PHARMACIST', hospital=cls.h1)
        User.objects.create_user(email='staff@h2.com', password='pw',
                                 role='PHARMACIST', hospital=cls.h2)

    def tearDown(self):
        clear_current_hospital()

    def test_list_shows_only_own_hospital_staff(self):
        c = Client(); c.force_login(self.admin1)
        resp = c.get(reverse('user_mgmt:user_list'))
        self.assertContains(resp, 'staff@h1.com')
        self.assertNotContains(resp, 'staff@h2.com')

    def test_superuser_without_hospital_sees_all_staff(self):
        root = User.objects.create_superuser(email='root@t.com', password='pw')
        c = Client(); c.force_login(root)
        resp = c.get(reverse('user_mgmt:user_list'))
        self.assertContains(resp, 'staff@h1.com')
        self.assertContains(resp, 'staff@h2.com')


class UserEditTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        cls.admin = User.objects.create_user(email='admin@h.com', password='pw',
                                             role='ADMIN', hospital=cls.h)
        cls.staff = User.objects.create_user(email='staff@h.com', password='original',
                                             role='PHARMACIST', hospital=cls.h)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.admin)

    def tearDown(self):
        clear_current_hospital()

    def _edit(self, **overrides):
        data = {
            'email': 'staff@h.com', 'first_name': 'S', 'last_name': 'T',
            'role': 'PHARMACIST', 'is_active': 'on', 'password': '',
        }
        data.update(overrides)
        return self.client.post(reverse('user_mgmt:user_edit', args=[self.staff.pk]), data)

    def test_blank_password_on_edit_keeps_the_existing_one(self):
        self._edit()
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('original'))

    def test_role_change_is_applied(self):
        self._edit(role='NURSE')
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, 'NURSE')

    def test_unticking_customize_restores_role_defaults(self):
        self.staff.custom_features = ['reports']
        self.staff.save()
        self._edit()
        self.staff.refresh_from_db()
        self.assertIsNone(self.staff.custom_features)
