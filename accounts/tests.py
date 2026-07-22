"""Tests for the permission engine and notifications.

`accounts.permissions` decides what every user in the product can reach, and the
sidebar is built from the same helpers — so a bug here either locks staff out or
hands them another department's data.

    python manage.py test accounts --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User, Notification
from accounts.permissions import (
    FEATURES, MODULES, MODULE_KEYS, CORE_FEATURES,
    default_features_for_role, effective_features, user_has_feature,
    enabled_feature_set,
)
from saas.models import Hospital


def _future():
    return date.today() + timedelta(days=365)


class RoleDefaultsTest(TestCase):
    def test_every_role_in_features_is_a_real_role(self):
        valid = {code for code, _label in User.ROLE_CHOICES}
        for key, roles in FEATURES.items():
            with self.subTest(feature=key):
                self.assertTrue(roles <= valid,
                                f"{key} references unknown role(s): {roles - valid}")

    def test_admin_gets_every_feature_by_default(self):
        self.assertEqual(default_features_for_role('ADMIN'), set(FEATURES))

    def test_pharmacist_has_pharmacy_but_not_clinical(self):
        feats = default_features_for_role('PHARMACIST')
        self.assertIn('pos', feats)
        self.assertIn('inventory', feats)
        self.assertNotIn('opd', feats)
        self.assertNotIn('lab', feats)

    def test_nurse_has_ward_only(self):
        feats = default_features_for_role('NURSE')
        self.assertIn('ward', feats)
        for locked in ('pos', 'billing', 'ipd', 'prescriptions'):
            self.assertNotIn(locked, feats)


class EffectiveFeaturesTest(TestCase):
    def test_superuser_has_everything(self):
        root = User.objects.create_superuser(email='r@t.com', password='pw')
        self.assertEqual(effective_features(root), set(FEATURES))
        self.assertTrue(user_has_feature(root, 'settings'))

    def test_custom_features_replace_role_defaults(self):
        u = User.objects.create_user(email='c@t.com', password='pw', role='PHARMACIST')
        u.custom_features = ['reports']
        u.save()
        self.assertEqual(effective_features(u), {'reports'})
        self.assertFalse(user_has_feature(u, 'pos'))     # role default revoked
        self.assertTrue(user_has_feature(u, 'reports'))

    def test_empty_custom_features_means_no_access(self):
        """`[]` is an explicit lock-out, not 'fall back to the role'."""
        u = User.objects.create_user(email='e@t.com', password='pw', role='ADMIN')
        u.custom_features = []
        u.save()
        self.assertEqual(effective_features(u), set())

    def test_none_custom_features_inherits_role(self):
        u = User.objects.create_user(email='n@t.com', password='pw', role='LABTECH')
        self.assertIsNone(u.custom_features)
        self.assertEqual(effective_features(u), default_features_for_role('LABTECH'))

    def test_unknown_feature_keys_are_ignored(self):
        u = User.objects.create_user(email='u@t.com', password='pw', role='ADMIN')
        u.custom_features = ['pos', 'not-a-real-feature']
        u.save()
        self.assertEqual(effective_features(u), {'pos'})


class ModuleGatingTest(TestCase):
    def test_no_modules_selected_means_all_on(self):
        self.assertEqual(enabled_feature_set([]), set(FEATURES))
        self.assertEqual(enabled_feature_set(None), set(FEATURES))

    def test_selecting_a_module_enables_exactly_its_features_plus_core(self):
        feats = enabled_feature_set(['pharmacy'])
        self.assertTrue(CORE_FEATURES <= feats)
        for key in ('pos', 'inventory', 'customers', 'suppliers'):
            self.assertIn(key, feats)
        for key in ('opd', 'lab', 'imaging', 'ipd'):
            self.assertNotIn(key, feats)

    def test_ipd_module_carries_the_ward_feature(self):
        self.assertIn('ward', enabled_feature_set(['ipd']))

    def test_every_module_feature_key_exists(self):
        for mkey, _label, _desc, fkeys in MODULES:
            for fk in fkeys:
                with self.subTest(module=mkey, feature=fk):
                    self.assertIn(fk, FEATURES)

    def test_module_keys_are_unique(self):
        self.assertEqual(len(MODULE_KEYS), len(set(MODULE_KEYS)))


class ModuleEnforcementTest(TestCase):
    """A module switched off is a 403 for everyone, admins included."""

    def test_disabled_module_blocks_even_an_admin(self):
        h = Hospital.objects.create(name='Pharmacy Only', slug='po',
                                    expiry_date=_future(),
                                    enabled_modules=['pharmacy'])
        admin = User.objects.create_user(email='po@t.com', password='pw',
                                         role='ADMIN', hospital=h)
        c = Client(); c.force_login(admin)
        self.assertEqual(c.get(reverse('medicine_list')).status_code, 200)   # pharmacy on
        self.assertEqual(c.get(reverse('patient_list')).status_code, 403)    # opd off
        self.assertEqual(c.get(reverse('lab:order_list')).status_code, 403)  # lab off

    def test_disabled_module_is_hidden_from_the_sidebar(self):
        """Nav and access come from the same helpers — no links into a 403."""
        h = Hospital.objects.create(name='Pharm', slug='ph2', expiry_date=_future(),
                                    enabled_modules=['pharmacy'])
        admin = User.objects.create_user(email='ph2@t.com', password='pw',
                                         role='ADMIN', hospital=h)
        c = Client(); c.force_login(admin)
        resp = c.get(reverse('medicine_list'))
        self.assertNotContains(resp, reverse('lab:order_list'))
        self.assertContains(resp, reverse('sale_create'))


class NotificationTest(TestCase):
    """Handoffs between departments rely on role-targeted notifications."""

    @classmethod
    def setUpTestData(cls):
        cls.h1 = Hospital.objects.create(name='H1', slug='h1', expiry_date=_future())
        cls.h2 = Hospital.objects.create(name='H2', slug='h2', expiry_date=_future())
        cls.recep1 = User.objects.create_user(email='r1@t.com', password='pw',
                                              role='RECEPTIONIST', hospital=cls.h1)
        cls.recep2 = User.objects.create_user(email='r2@t.com', password='pw',
                                              role='RECEPTIONIST', hospital=cls.h2)
        cls.doc1 = User.objects.create_user(email='d1@t.com', password='pw',
                                            role='DOCTOR', hospital=cls.h1)

    def test_send_to_role_reaches_only_that_role_in_that_hospital(self):
        Notification.send_to_role(self.h1, 'RECEPTIONIST', 'Admission advised', '/ipd/')
        self.assertEqual(self.recep1.notifications.count(), 1)
        self.assertEqual(self.recep2.notifications.count(), 0)   # other tenant
        self.assertEqual(self.doc1.notifications.count(), 0)     # other role

    def test_inactive_users_are_skipped(self):
        self.recep1.is_active = False
        self.recep1.save()
        Notification.send_to_role(self.h1, 'RECEPTIONIST', 'msg')
        self.assertEqual(self.recep1.notifications.count(), 0)

    def test_no_hospital_sends_nothing(self):
        Notification.send_to_role(None, 'RECEPTIONIST', 'msg')
        self.assertEqual(Notification.objects.count(), 0)

    def test_link_is_stored(self):
        Notification.send_to_role(self.h1, 'RECEPTIONIST', 'msg', '/ipd/requests/')
        self.assertEqual(self.recep1.notifications.first().link, '/ipd/requests/')


class UserModelTest(TestCase):
    def test_email_is_the_login_field(self):
        self.assertEqual(User.USERNAME_FIELD, 'email')
        User.objects.create_user(email='login@t.com', password='pw')
        self.assertTrue(Client().login(email='login@t.com', password='pw'))

    def test_default_role_is_pharmacist(self):
        self.assertEqual(User.objects.create_user(email='def@t.com', password='pw').role,
                         'PHARMACIST')
