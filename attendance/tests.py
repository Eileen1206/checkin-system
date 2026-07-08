from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.cache import cache
from attendance.models import (
    Employee, Customer, AttendanceRecord, DeliverySession, DeliveryTask,
)
from django.utils import timezone
from attendance.utils import routing


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


class RouteDriveCacheTest(TestCase):
    """行車時間預估：只讀路徑不得同步呼叫 ORS，避免 worker timeout"""

    def setUp(self):
        cache.clear()
        # 公司座標（get_office_coords 由 A000 客戶取得）
        Customer.objects.create(
            customer_id='A000', name='公司', address='公司地址',
            lat='25.033000', lng='121.565000',
        )
        self.customer = Customer.objects.create(
            customer_id='C001', name='客戶一', address='客戶地址',
            lat='25.040000', lng='121.560000',
        )

    def test_cache_only_never_calls_ors(self):
        """cache_only=True 且快取未命中時回傳 None，且不建立 ORS client。"""
        with patch.object(routing, 'get_client',
                          side_effect=AssertionError('不應呼叫 ORS')) as m:
            result = routing.get_route_drive_minutes(
                [self.customer], cache_only=True
            )
        self.assertIsNone(result)
        m.assert_not_called()

    def test_cache_only_returns_warmed_value(self):
        """寫入路徑預熱後，cache_only 應直接回傳快取值、仍不呼叫 ORS。"""
        fake_response = {'routes': [{'summary': {'duration': 600}}]}  # 10 分鐘
        with patch.object(routing, 'get_client') as get_client:
            get_client.return_value.directions.return_value = fake_response
            warmed = routing.get_route_drive_minutes([self.customer])
        self.assertEqual(warmed, 10.0)

        with patch.object(routing, 'get_client',
                          side_effect=AssertionError('不應呼叫 ORS')):
            cached = routing.get_route_drive_minutes(
                [self.customer], cache_only=True
            )
        self.assertEqual(cached, 10.0)


class DashboardNoBlockingCallTest(TestCase):
    """儀表板即使 ORS 掛掉也要能正常渲染（不因外部 API 卡住而 500/timeout）"""

    def setUp(self):
        cache.clear()
        admin = User.objects.create_user(
            username='boss', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss', password='pass12345')

        Customer.objects.create(
            customer_id='A000', name='公司', address='公司地址',
            lat='25.033000', lng='121.565000',
        )
        customer = Customer.objects.create(
            customer_id='C001', name='客戶一', address='客戶地址',
            lat='25.040000', lng='121.560000',
        )
        driver_user = User.objects.create_user(username='driver', password='x')
        employee = Employee.objects.create(
            user=driver_user, employee_id='D001',
            department='外送', is_delivery=True,
        )
        today = timezone.localdate()
        session = DeliverySession.objects.create(
            employee=employee, date=today, trip_number=1,
            pushed_at=timezone.now(),
        )
        DeliveryTask.objects.create(
            employee=employee, date=today, order=1,
            customer=customer, customer_name=customer.name,
            address=customer.address, status='pending', session=session,
        )

    def test_dashboard_renders_when_ors_unavailable(self):
        with patch.object(routing, 'get_client',
                          side_effect=AssertionError('儀表板不應呼叫 ORS')):
            resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)
