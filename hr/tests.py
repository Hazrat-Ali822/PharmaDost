"""Staff HR — attendance, leave, payroll.

    python manage.py test hr --settings=pharma_mgmt.test_settings
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital, set_current_hospital

from .models import Attendance, LeaveRequest, SalaryPayment, StaffProfile


def _future():
    return date.today() + timedelta(days=365)


class HrTest(TestCase):
    def setUp(self):
        self.h = Hospital.objects.create(name='H', slug='h', expiry_date=_future())
        set_current_hospital(self.h)
        self.admin = User.objects.create_user(email='a@h.com', password='pw', role='ADMIN', hospital=self.h)
        self.nurse = User.objects.create_user(email='n@h.com', password='pw', role='NURSE', hospital=self.h)

    def tearDown(self):
        clear_current_hospital()

    def _client(self, user=None):
        c = Client(); c.force_login(user or self.admin); return c

    def test_salary_net_is_basic_plus_allow_minus_deduct(self):
        s = SalaryPayment.objects.create(user=self.nurse, period='August 2026',
                                         basic=Decimal('40000'), allowances=Decimal('5000'),
                                         deductions=Decimal('2000'), hospital=self.h)
        self.assertEqual(s.net, Decimal('43000'))

    def test_attendance_grid_upserts_one_row_per_day(self):
        day = date.today()
        url = reverse('hr_attendance')
        self._client().post(url, {'date': day.strftime('%Y-%m-%d'),
                                  f'status_{self.nurse.id}': 'PRESENT'})
        self._client().post(url, {'date': day.strftime('%Y-%m-%d'),
                                  f'status_{self.nurse.id}': 'ABSENT'})
        rows = Attendance.objects.filter(user=self.nurse, date=day)
        self.assertEqual(rows.count(), 1)                 # upsert, not duplicate
        self.assertEqual(rows.first().status, 'ABSENT')

    def test_leave_approve_records_decider(self):
        lv = LeaveRequest.objects.create(user=self.nurse, start_date=date.today(),
                                         end_date=date.today() + timedelta(days=2),
                                         leave_type='SICK', hospital=self.h)
        self.assertEqual(lv.days, 3)
        self._client().post(reverse('hr_leave_decide', args=[lv.pk, 'approve']))
        lv.refresh_from_db()
        self.assertEqual(lv.status, 'APPROVED')
        self.assertEqual(lv.decided_by_id, self.admin.id)

    def test_salary_create_makes_payslip(self):
        resp = self._client().post(reverse('hr_salary_create'), {
            'user': self.nurse.id, 'period': 'August 2026', 'basic': '40000',
            'allowances': '0', 'deductions': '0', 'paid_on': date.today().strftime('%Y-%m-%d'),
            'method': 'CASH', 'note': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(SalaryPayment.objects.filter(user=self.nurse).exists())

    def test_feature_gate(self):
        pharm = User.objects.create_user(email='p@h.com', password='pw', role='PHARMACIST', hospital=self.h)
        self.assertEqual(self._client(pharm).get(reverse('hr_staff_list')).status_code, 403)

    def test_profile_scoped_to_hospital(self):
        StaffProfile.objects.create(user=self.nurse, monthly_salary=Decimal('30000'), hospital=self.h)
        other = Hospital.objects.create(name='O', slug='o', expiry_date=_future())
        set_current_hospital(other)
        self.assertEqual(StaffProfile.objects.count(), 0)
        set_current_hospital(self.h)
        self.assertEqual(StaffProfile.objects.count(), 1)
