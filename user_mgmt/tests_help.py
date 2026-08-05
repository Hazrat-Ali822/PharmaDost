"""The in-app user guide is reachable by every role and is role-aware."""
from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from saas.models import Hospital


class HelpCenterTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h',
                                         expiry_date=date.today() + timedelta(days=365))

    def _user(self, role):
        u = User.objects.create_user(email=f'{role}@x.com', password='pw',
                                     role=role, hospital=self.h)
        c = Client(); c.login(email=f'{role}@x.com', password='pw')
        return c

    def test_every_role_can_open_the_guide(self):
        for role in ['ADMIN', 'RECEPTIONIST', 'DOCTOR', 'NURSE', 'PHARMACIST',
                     'LABTECH', 'SONOGRAPHER', 'ACCOUNTANT', 'WHOLESALE']:
            page = self._user(role).get('/manage/help/')
            self.assertEqual(page.status_code, 200, role)
            self.assertContains(page, 'How to use')

    def test_doctor_sees_doctor_quick_links_not_admin_only_ones(self):
        page = self._user('DOCTOR').get('/manage/help/')
        self.assertContains(page, 'Doctor / OPD')
        # the doctor's role card should not point them at the admin-only sections
        self.assertNotContains(page, '#settings">Settings')

    def test_login_required(self):
        page = Client().get('/manage/help/')
        self.assertEqual(page.status_code, 302)
