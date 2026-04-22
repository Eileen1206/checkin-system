from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from attendance.models import Employee, Customer, AttendanceRecord
from django.utils import timezone


class LoginRequiredTest(TestCase):
    """未登入時應被導向登入頁"""

    def test_dashboard_redirects_to_login(self):
        resp = self.client.get('/dashboard/')
        self.assertRedirects(resp, '/accounts/login/?next=/dashboard/')

    def test_employee_list_redirects_to_login(self):
        resp = self.client.get('/dashboard/employees/')
        self.assertEqual(resp.status_code, 302)

    def test_salary_redirects_to_login(self):
        resp = self.client.get('/dashboard/salary/')
        self.assertEqual(resp.status_code, 302)


class DashboardAccessTest(TestCase):
    """登入後可以正常開啟各頁面"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpass123',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='testadmin', password='testpass123')

    def test_dashboard_index(self):
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)

    def test_employee_list(self):
        resp = self.client.get('/dashboard/employees/')
        self.assertEqual(resp.status_code, 200)

    def test_customer_list(self):
        resp = self.client.get('/dashboard/customers/')
        self.assertEqual(resp.status_code, 200)

    def test_salary_page(self):
        resp = self.client.get('/dashboard/salary/')
        self.assertEqual(resp.status_code, 200)

    def test_binding_list(self):
        resp = self.client.get('/dashboard/binding/')
        self.assertEqual(resp.status_code, 200)


class EmployeeModelTest(TestCase):
    """Employee model 基本行為"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='emp1', password='pass',
            first_name='小明', last_name='王',
        )
        self.employee = Employee.objects.create(
            user=self.user,
            employee_id='E001',
            department='業務',
            employment_type='monthly',
            monthly_salary=30000,
        )

    def test_str(self):
        self.assertIn('E001', str(self.employee))

    def test_is_delivery_default_false(self):
        self.assertFalse(self.employee.is_delivery)

    def test_fuel_allowance_default_zero(self):
        self.assertEqual(self.employee.fuel_daily_allowance, 0)


class CustomerModelTest(TestCase):
    """Customer model 基本行為"""

    def setUp(self):
        self.customer = Customer.objects.create(
            customer_id='C001',
            name='測試客戶',
            address='台中市西區',
        )

    def test_is_active_default_true(self):
        self.assertTrue(self.customer.is_active)


class AttendanceRecordTest(TestCase):
    """打卡紀錄基本行為"""

    def setUp(self):
        user = User.objects.create_user(username='emp2', password='pass')
        self.employee = Employee.objects.create(
            user=user,
            employee_id='E002',
            department='倉儲',
        )

    def test_create_clock_in(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee,
            record_type='clock_in',
        )
        self.assertEqual(record.record_type, 'clock_in')
        self.assertTrue(record.is_valid)

    def test_get_today_records(self):
        AttendanceRecord.objects.create(
            employee=self.employee,
            record_type='clock_in',
        )
        records = AttendanceRecord.get_today_records(self.employee)
        self.assertEqual(records.count(), 1)


class WebhookSignatureTest(TestCase):
    """LINE Webhook 簽名驗證"""

    def test_webhook_rejects_invalid_signature(self):
        resp = self.client.post(
            '/attendance/webhook/',
            data=b'{}',
            content_type='application/json',
            HTTP_X_LINE_SIGNATURE='invalid-signature',
        )
        self.assertEqual(resp.status_code, 400)

    def test_webhook_rejects_missing_signature(self):
        resp = self.client.post(
            '/attendance/webhook/',
            data=b'{}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
