from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.cache import cache
from attendance.models import (
    Employee, Customer, AttendanceRecord, DeliverySession, DeliveryTask, LeaveRecord,
)
from django.utils import timezone
from datetime import date, datetime, timedelta
from attendance.utils import routing, scheduling, payroll


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

    def test_webhook_returns_200_on_handler_error(self):
        """處理事件時發生非簽章錯誤，webhook 仍須回 200，避免 LINE 反覆重送。"""
        from attendance import views
        with patch.object(views.handler, 'handle', side_effect=RuntimeError('boom')):
            resp = self.client.post(
                '/attendance/webhook/',
                data=b'{}',
                content_type='application/json',
                HTTP_X_LINE_SIGNATURE='whatever',
            )
        self.assertEqual(resp.status_code, 200)


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

    def test_client_has_bounded_retry_window(self):
        """ORS client 必須設短 retry_timeout，避免伺服器掛掉時重試到 worker timeout。"""
        with patch.object(routing.openrouteservice, 'Client') as Client:
            routing.get_client()
        kwargs = Client.call_args.kwargs
        self.assertIn('retry_timeout', kwargs)
        self.assertLessEqual(kwargs['retry_timeout'], 10)
        self.assertFalse(kwargs.get('retry_over_query_limit', True))

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


class DeliveryPushErrorTest(TestCase):
    """LINE 推播失敗時，delivery_push 須回滾並提示，而非回 500 錯誤頁"""

    def setUp(self):
        cache.clear()
        admin = User.objects.create_user(
            username='boss2', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss2', password='pass12345')

        driver_user = User.objects.create_user(
            username='driver2', password='x', first_name='一', last_name='王',
        )
        self.employee = Employee.objects.create(
            user=driver_user, employee_id='D009',
            department='外送', is_delivery=True, line_user_id='Utest009',
        )
        customer = Customer.objects.create(
            customer_id='C009', name='客戶九', address='客戶地址',
            lat='25.040000', lng='121.560000',
        )
        DeliveryTask.objects.create(
            employee=self.employee, date=timezone.localdate(), order=1,
            customer=customer, customer_name=customer.name,
            address=customer.address, status='pending',
        )

    def test_push_failure_rolls_back_and_avoids_500(self):
        from attendance.dashboard_views import delivery_views
        with patch.object(delivery_views, 'MessagingApi') as MessagingApi:
            MessagingApi.return_value.push_message.side_effect = Exception('LINE 500')
            resp = self.client.post('/dashboard/delivery/push/', {
                'employee_id': self.employee.pk,
                'date': str(timezone.localdate()),
            })
        # 應為轉址（回送貨規劃頁），而非 500 錯誤頁
        self.assertEqual(resp.status_code, 302)
        # 本趟已回滾，不留半殘趟次
        self.assertEqual(DeliverySession.objects.count(), 0)
        # 任務退回未推播狀態（session 為空、仍為 pending）
        task = DeliveryTask.objects.get()
        self.assertIsNone(task.session)
        self.assertEqual(task.status, 'pending')


class SchedulingLogicTest(TestCase):
    """一例一休判定：週日公休為例假，只看週一~週六是否排了休息日"""

    def _week(self, anchor):
        """由任一日期取得其所在週的週一~週日 7 個 date。"""
        monday = anchor - timedelta(days=anchor.weekday())
        return [monday + timedelta(days=i) for i in range(7)]

    def test_no_weekday_leave_is_missing(self):
        """整週沒排平日休 → 缺休（即使固定週休二也一樣）。"""
        week = self._week(date(2026, 8, 15))
        st = scheduling.week_rest_status(week, set(), required=1)
        self.assertFalse(st['compliant'])
        self.assertEqual(st['weekday_rest'], 0)
        self.assertEqual(st['shortfall'], 1)

    def test_one_weekday_leave_is_compliant(self):
        week = self._week(date(2026, 8, 15))
        tuesday = week[1]
        st = scheduling.week_rest_status(week, {tuesday}, required=1)
        self.assertTrue(st['compliant'])
        self.assertEqual(st['weekday_rest'], 1)

    def test_sunday_leave_does_not_count(self):
        """週日已是公休（例假），週日放假不算平日休息日。"""
        week = self._week(date(2026, 8, 15))
        sunday = week[6]
        st = scheduling.week_rest_status(week, {sunday}, required=1)
        self.assertFalse(st['compliant'])
        self.assertEqual(st['weekday_rest'], 0)

    def test_required_two_needs_two_weekday_rests(self):
        week = self._week(date(2026, 8, 15))
        st = scheduling.week_rest_status(week, {week[1]}, required=2)
        self.assertFalse(st['compliant'])
        self.assertEqual(st['shortfall'], 1)

    def test_iter_month_weeks_covers_month(self):
        weeks = scheduling.iter_month_weeks(2026, 8)
        self.assertTrue(weeks)
        for wk in weeks:
            self.assertEqual(len(wk['dates']), 7)
            self.assertEqual(wk['dates'][0].weekday(), 0)   # 週一起算
            self.assertEqual(wk['dates'][6].weekday(), 6)   # 週日結束
        # 8/1 與 8/31 都要被某一週涵蓋
        all_days = {d for wk in weeks for d in wk['dates']}
        self.assertIn(date(2026, 8, 1), all_days)
        self.assertIn(date(2026, 8, 31), all_days)

    def test_understaffed_days_threshold(self):
        d1, d2 = date(2026, 8, 10), date(2026, 8, 11)
        by_date = scheduling.group_leaves_by_date([
            (d1, '甲'), (d1, '乙'),   # 2 人
            (d2, '丙'),               # 1 人
        ])
        result = scheduling.understaffed_days(by_date, threshold=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['date'], d1)
        self.assertEqual(result[0]['count'], 2)


class LeaveCalendarStatsViewTest(TestCase):
    """請假月曆的一例一休統計區塊"""

    def setUp(self):
        admin = User.objects.create_user(
            username='boss3', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss3', password='pass12345')

        # 週一~週六的員工，8 月完全沒排平日休 → 應有缺休週
        u1 = User.objects.create_user(username='w1', password='x', first_name='一', last_name='王')
        self.emp1 = Employee.objects.create(
            user=u1, employee_id='W1', department='外送', work_days='0,1,2,3,4,5',
        )
        u2 = User.objects.create_user(username='w2', password='x', first_name='二', last_name='李')
        self.emp2 = Employee.objects.create(
            user=u2, employee_id='W2', department='外送', work_days='0,1,2,3,4,5',
        )
        # 同一天兩人休 → 人力吃緊
        LeaveRecord.objects.create(employee=self.emp1, date=date(2026, 8, 11))
        LeaveRecord.objects.create(employee=self.emp2, date=date(2026, 8, 11))

    def test_stats_in_context(self):
        resp = self.client.get('/dashboard/leave/?year=2026&month=8')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('week_compliance', resp.context)
        self.assertIn('understaffed', resp.context)
        # 8/11 兩人休 → 人力吃緊清單有一筆
        understaffed = resp.context['understaffed']
        self.assertTrue(any(item['date'] == date(2026, 8, 11) for item in understaffed))
        # 週一~週六且該員工只在 8/11 排一天休 → 其他週應有缺休
        rows = {r['employee'].pk: r for r in resp.context['week_compliance']}
        self.assertGreater(rows[self.emp2.pk]['miss_count'], 0)


class EmployeeDeactivateTest(TestCase):
    """停用員工：全系統隱藏、資料保留、可篩選 / 復職"""

    def setUp(self):
        admin = User.objects.create_user(
            username='boss4', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss4', password='pass12345')

        u_active = User.objects.create_user(username='act', password='x', first_name='在', last_name='職')
        self.active_emp = Employee.objects.create(user=u_active, employee_id='A1', department='業務')
        u_inactive = User.objects.create_user(username='inact', password='x', first_name='離', last_name='職')
        self.inactive_emp = Employee.objects.create(
            user=u_inactive, employee_id='Z9', department='業務', is_active=False,
        )

    def test_default_is_active_true(self):
        self.assertTrue(self.active_emp.is_active)

    def test_active_manager_excludes_inactive_but_data_kept(self):
        active_ids = set(Employee.active.values_list('pk', flat=True))
        self.assertIn(self.active_emp.pk, active_ids)
        self.assertNotIn(self.inactive_emp.pk, active_ids)
        # 預設 manager 仍看得到（資料保留、可查/復職）
        self.assertIn(self.inactive_emp.pk, set(Employee.objects.values_list('pk', flat=True)))

    def test_employee_list_hides_inactive_by_default(self):
        resp = self.client.get('/dashboard/employees/')
        ids = [e.pk for e in resp.context['employees']]
        self.assertIn(self.active_emp.pk, ids)
        self.assertNotIn(self.inactive_emp.pk, ids)

    def test_employee_list_shows_inactive_with_flag(self):
        resp = self.client.get('/dashboard/employees/?show_inactive=1')
        ids = [e.pk for e in resp.context['employees']]
        self.assertIn(self.inactive_emp.pk, ids)

    def test_leave_calendar_roster_excludes_inactive(self):
        resp = self.client.get('/dashboard/leave/?year=2026&month=8')
        ids = [e.pk for e in resp.context['employees']]
        self.assertNotIn(self.inactive_emp.pk, ids)

    def test_salary_hides_inactive_by_default(self):
        resp = self.client.get('/dashboard/salary/')
        emp_ids = [e.pk for e in resp.context['employees']]
        self.assertIn(self.active_emp.pk, emp_ids)
        self.assertNotIn(self.inactive_emp.pk, emp_ids)

    def test_toggle_active_deactivates_and_reactivates(self):
        resp = self.client.post(f'/dashboard/employees/{self.active_emp.pk}/toggle-active/')
        self.assertEqual(resp.status_code, 302)
        self.active_emp.refresh_from_db()
        self.assertFalse(self.active_emp.is_active)
        # 再按一次 → 復職
        self.client.post(f'/dashboard/employees/{self.active_emp.pk}/toggle-active/')
        self.active_emp.refresh_from_db()
        self.assertTrue(self.active_emp.is_active)


class AdminOnlyEmployeeTest(TestCase):
    """純管理帳號：在職但不列入出勤/報表，仍在員工列表可見"""

    def setUp(self):
        admin = User.objects.create_user(
            username='boss5', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss5', password='pass12345')

        u1 = User.objects.create_user(username='track', password='x', first_name='一', last_name='般')
        self.tracked_emp = Employee.objects.create(user=u1, employee_id='T1', department='業務')
        u2 = User.objects.create_user(username='mgr', password='x', first_name='純', last_name='管')
        self.admin_emp = Employee.objects.create(
            user=u2, employee_id='M1', department='管理', is_report_visible=False,
        )

    def test_default_is_report_visible_true(self):
        self.assertTrue(self.tracked_emp.is_report_visible)

    def test_tracked_manager_excludes_admin_only(self):
        ids = set(Employee.tracked.values_list('pk', flat=True))
        self.assertIn(self.tracked_emp.pk, ids)
        self.assertNotIn(self.admin_emp.pk, ids)
        # active（員工列表用）仍含純管理員
        self.assertIn(self.admin_emp.pk, set(Employee.active.values_list('pk', flat=True)))

    def test_employee_list_shows_admin_only(self):
        resp = self.client.get('/dashboard/employees/')
        ids = [e.pk for e in resp.context['employees']]
        self.assertIn(self.admin_emp.pk, ids)

    def test_reports_exclude_admin_only(self):
        # 請假月曆
        leave = self.client.get('/dashboard/leave/?year=2026&month=8')
        self.assertNotIn(self.admin_emp.pk, [e.pk for e in leave.context['employees']])
        # 薪資
        sal = self.client.get('/dashboard/salary/')
        self.assertNotIn(self.admin_emp.pk, [e.pk for e in sal.context['employees']])
        # 出勤報表（reports app）的員工下拉
        rpt = self.client.get('/reports/')
        self.assertNotIn(self.admin_emp.pk, [e.pk for e in rpt.context['employees']])
        self.assertIn(self.tracked_emp.pk, [e.pk for e in rpt.context['employees']])


class OnboardWizardTest(TestCase):
    """入職精靈：新增員工＋可選立即產生綁定碼"""

    def setUp(self):
        admin = User.objects.create_user(
            username='boss6', password='pass12345',
            is_staff=True, is_superuser=True,
        )
        self.client.login(username='boss6', password='pass12345')

    def _base(self, **over):
        data = {
            'need_login': 'on', 'username': 'newguy', 'password': 'pw123456',
            'first_name': '新', 'last_name': '人',
            'employee_id': 'N1', 'department': '業務',
            'employment_type': 'monthly',
        }
        data.update(over)
        return data

    def test_wizard_with_token_shows_qr(self):
        from attendance.models import BindingToken
        resp = self.client.post('/dashboard/employees/add/', self._base(make_token='on'))
        self.assertEqual(resp.status_code, 200)  # 綁定 QR done 頁
        emp = Employee.objects.get(employee_id='N1')
        self.assertTrue(emp.is_report_visible)
        self.assertEqual(BindingToken.objects.filter(employee=emp).count(), 1)

    def test_wizard_admin_only_without_token_redirects(self):
        resp = self.client.post('/dashboard/employees/add/',
                                self._base(username='mgr2', employee_id='M2', admin_only='on'))
        self.assertEqual(resp.status_code, 302)  # 轉址回列表
        emp = Employee.objects.get(employee_id='M2')
        self.assertFalse(emp.is_report_visible)

    def test_wizard_sets_work_days(self):
        self.client.post('/dashboard/employees/add/',
                         self._base(username='wd', employee_id='WD1', work_days=['0', '2', '4']))
        emp = Employee.objects.get(employee_id='WD1')
        self.assertEqual(emp.work_days, '0,2,4')


class PayrollLaborLawTest(TestCase):
    """勞基法特休天數 / 加班費 / 工資基準（純函式）"""

    def setUp(self):
        u1 = User.objects.create_user('pm', password='x')
        self.monthly = Employee.objects.create(
            user=u1, employee_id='PM', department='x',
            employment_type='monthly', monthly_salary=36000, work_days='0,1,2,3,4,5')
        u2 = User.objects.create_user('ph', password='x')
        self.hourly = Employee.objects.create(
            user=u2, employee_id='PH', department='x',
            employment_type='hourly', hourly_rate=200, work_days='0,1,2,3,4,5')

    def test_annual_leave_days_brackets(self):
        hire = date(2020, 1, 1)
        self.assertEqual(payroll.annual_leave_days(hire, date(2020, 6, 1)), 0)   # <6mo
        self.assertEqual(payroll.annual_leave_days(hire, date(2020, 7, 1)), 3)   # 6mo
        self.assertEqual(payroll.annual_leave_days(hire, date(2021, 1, 1)), 7)   # 1yr
        self.assertEqual(payroll.annual_leave_days(hire, date(2022, 1, 1)), 10)  # 2yr
        self.assertEqual(payroll.annual_leave_days(hire, date(2023, 1, 1)), 14)  # 3yr
        self.assertEqual(payroll.annual_leave_days(hire, date(2025, 1, 1)), 15)  # 5yr
        self.assertEqual(payroll.annual_leave_days(hire, date(2030, 1, 1)), 16)  # 10yr
        self.assertEqual(payroll.annual_leave_days(hire, date(2044, 1, 1)), 30)  # 24yr→30
        self.assertEqual(payroll.annual_leave_days(hire, date(2050, 1, 1)), 30)  # 上限

    def test_service_length(self):
        self.assertEqual(payroll.service_length(date(2020, 1, 1), date(2023, 4, 1)), (3, 3))

    def test_wages(self):
        self.assertAlmostEqual(payroll.hourly_wage(self.monthly), 150.0)   # 36000/240
        self.assertAlmostEqual(payroll.daily_wage(self.monthly), 1200.0)   # 36000/30
        self.assertAlmostEqual(payroll.hourly_wage(self.hourly), 200.0)
        self.assertAlmostEqual(payroll.daily_wage(self.hourly), 1600.0)    # 200×8

    def test_classify_day(self):
        monday = date(2026, 8, 15)
        monday = monday - timedelta(days=monday.weekday())
        sunday = monday + timedelta(days=6)
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(sunday.weekday(), 6)
        self.assertEqual(payroll.classify_day(self.monthly, monday, set(), set()), '平日')
        self.assertEqual(payroll.classify_day(self.monthly, sunday, set(), set()), '例假')
        self.assertEqual(payroll.classify_day(self.monthly, monday, {monday}, set()), '國定假日')
        self.assertEqual(payroll.classify_day(self.monthly, monday, set(), {monday}), '休息日')

    def test_weekday_ot_full(self):
        # 全額：11h → 3h 加班（前2×4/3 + 1×5/3）
        self.assertAlmostEqual(payroll.weekday_ot(11, 200),
                               200 * (2 * 4 / 3 + 1 * 5 / 3), places=2)
        # 勞動部範例：時薪196、8.5h → 0.5h 加班 = 130.67
        self.assertAlmostEqual(payroll.weekday_ot(8.5, 196), 0.5 * 196 * 4 / 3, places=2)
        self.assertEqual(payroll.weekday_ot(8, 200), 0)         # 未超過 8h 無加班

    def test_restday_ot_full(self):
        # 10h 休息日：前2×4/3、3~8(6h)×5/3、9~10(2h)×8/3
        self.assertAlmostEqual(payroll.restday_ot(10, 200),
                               200 * (2 * 4 / 3 + 6 * 5 / 3 + 2 * 8 / 3), places=2)

    def test_holiday_ot(self):
        # 月薪 8h 國定假日 → 加發一日日薪
        self.assertAlmostEqual(payroll.holiday_ot(8, 150, 1200, True), 1200, places=2)
        # 時薪 8h → 加倍
        self.assertAlmostEqual(payroll.holiday_ot(8, 196, 0, False), 8 * 196 * 2, places=2)


class PayrollViewTest(TestCase):
    """特休結算頁 / 國定假日管理 / 薪資含加班費"""

    def setUp(self):
        admin = User.objects.create_user(
            username='boss7', password='pass12345', is_staff=True, is_superuser=True)
        self.client.login(username='boss7', password='pass12345')
        u = User.objects.create_user('emp_al', password='x', first_name='特', last_name='休')
        self.emp = Employee.objects.create(
            user=u, employee_id='AL1', department='x',
            employment_type='monthly', monthly_salary=30000, hire_date=date(2020, 1, 1))

    def test_annual_leave_settlement_page(self):
        resp = self.client.get(f'/dashboard/annual-leave/?employee_id={self.emp.pk}&as_of=2024-06-01')
        self.assertEqual(resp.status_code, 200)
        s = resp.context['settlement']
        self.assertTrue(s['has_hire_date'])
        self.assertEqual(s['days'], 14)        # 2020→2024 滿4年 = 14 天
        self.assertEqual(s['daily_wage'], 1000)  # 30000/30
        self.assertEqual(s['payout'], 14000)     # 14 × 1000

    def test_holiday_add(self):
        from attendance.models import Holiday
        resp = self.client.post('/dashboard/holidays/', {
            'action': 'add', 'dates': '2026-01-01\n2026-02-28', 'name': '測試'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Holiday.objects.count(), 2)

    def test_holiday_import_year(self):
        """一鍵匯入：只收 isHoliday 且有節日名稱者，並補上勞動節。"""
        from attendance.models import Holiday
        from attendance.dashboard_views import payroll_views
        fake = [
            {'date': '20260101', 'isHoliday': True,  'description': '開國紀念日'},
            {'date': '20260103', 'isHoliday': True,  'description': ''},          # 一般週末 → 跳過
            {'date': '20260105', 'isHoliday': False, 'description': ''},          # 上班日 → 跳過
        ]
        with patch.object(payroll_views.requests, 'get') as g:
            g.return_value.json.return_value = fake
            g.return_value.raise_for_status.return_value = None
            resp = self.client.post('/dashboard/holidays/',
                                    {'action': 'import', 'import_year': '2026'})
        self.assertEqual(resp.status_code, 302)
        dates = set(Holiday.objects.values_list('date', flat=True))
        self.assertIn(date(2026, 1, 1), dates)     # 國定假日
        self.assertIn(date(2026, 5, 1), dates)     # 勞動節自動補
        self.assertNotIn(date(2026, 1, 3), dates)  # 無名稱的週末不匯入
        self.assertEqual(len(dates), 2)

    def test_holiday_import_uses_selected_year(self):
        """匯入年度由下拉決定：選 2027 就抓 2027，且勞動節補在該年。"""
        from attendance.models import Holiday
        from attendance.dashboard_views import payroll_views
        with patch.object(payroll_views, '_import_taiwan_holidays',
                          wraps=payroll_views._import_taiwan_holidays) as spy, \
             patch.object(payroll_views.requests, 'get') as g:
            g.return_value.json.return_value = [
                {'date': '20270101', 'isHoliday': True, 'description': '開國紀念日'},
            ]
            g.return_value.raise_for_status.return_value = None
            self.client.post('/dashboard/holidays/',
                             {'action': 'import', 'import_year': '2027'})
        spy.assert_called_once_with(2027)
        self.assertIn('2027', g.call_args[0][0])          # 取用 2027 年度資料
        dates = set(Holiday.objects.values_list('date', flat=True))
        self.assertIn(date(2027, 1, 1), dates)
        self.assertIn(date(2027, 5, 1), dates)            # 勞動節補在所選年度

    def test_holiday_import_failure_shows_error(self):
        """外部資料抓不到時不得 500，應提示錯誤並轉址。"""
        from attendance.dashboard_views import payroll_views
        with patch.object(payroll_views.requests, 'get', side_effect=Exception('boom')):
            resp = self.client.post('/dashboard/holidays/',
                                    {'action': 'import', 'import_year': '2026'})
        self.assertEqual(resp.status_code, 302)

    def test_salary_page_renders_without_computing(self):
        """薪資頁應先秒開（只帶員工名單），計算交給 API。"""
        resp = self.client.get('/dashboard/salary/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('employees', resp.context)
        self.assertNotIn('results', resp.context)

    def test_salary_calc_api(self):
        resp = self.client.get(
            f'/dashboard/salary/api/calc/?employee_id={self.emp.pk}&year=2024&month=6')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        for key in ('base', 'overtime', 'deduction', 'total', 'name'):
            self.assertIn(key, data)

    def test_salary_calc_api_bad_params(self):
        resp = self.client.get('/dashboard/salary/api/calc/?employee_id=999999&year=2024&month=6')
        self.assertEqual(resp.status_code, 400)

    def test_salary_row_has_overtime_key(self):
        from attendance.dashboard_views.salary_views import _salary_row
        r = _salary_row(self.emp, 2024, 6)
        self.assertIn('overtime', r)
        self.assertIn('overtime_tiers', r)

    def test_monthly_overtime_returns_tiers(self):
        ot = payroll.monthly_overtime(self.emp, 2024, 6)
        self.assertIn('tiers', ot)
        self.assertEqual(
            set(ot['tiers'].keys()),
            {'weekday_1_2', 'weekday_3plus', 'restday_1_2', 'restday_3_8', 'restday_9_12', 'holiday'},
        )

    def test_salary_detail_page(self):
        resp = self.client.get(f'/dashboard/salary/{self.emp.pk}/detail/?year=2024&month=6')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('weekday_tiers', resp.context)
        self.assertIn('restday_tiers', resp.context)


class LeaveKindModelTest(TestCase):
    """排休／請假的判定與標示"""

    def setUp(self):
        u = User.objects.create_user(username='lk1', password='x', first_name='假', last_name='測')
        self.emp = Employee.objects.create(user=u, employee_id='LK1', department='外送')

    def test_rest_is_always_full_day(self):
        lr = LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, 1))
        self.assertTrue(lr.is_rest)
        self.assertTrue(lr.is_full_day)
        self.assertEqual(lr.short_label, '休')
        self.assertEqual(lr.hours_label, '整天')

    def test_full_day_leave(self):
        lr = LeaveRecord.objects.create(
            employee=self.emp, date=date(2026, 9, 2),
            kind=LeaveRecord.KIND_LEAVE, leave_type='sick', hours=8,
        )
        self.assertFalse(lr.is_rest)
        self.assertTrue(lr.is_full_day)
        self.assertEqual(lr.short_label, '假')
        self.assertEqual(lr.hours_label, '整天')

    def test_partial_leave_is_not_full_day(self):
        lr = LeaveRecord.objects.create(
            employee=self.emp, date=date(2026, 9, 3),
            kind=LeaveRecord.KIND_LEAVE, leave_type='personal', hours=2,
        )
        self.assertFalse(lr.is_full_day)
        self.assertEqual(lr.short_label, '2h')
        self.assertEqual(lr.hours_label, '2 小時')

    def test_half_day_label(self):
        lr = LeaveRecord.objects.create(
            employee=self.emp, date=date(2026, 9, 4),
            kind=LeaveRecord.KIND_LEAVE, hours=4,
        )
        self.assertEqual(lr.hours_label, '半天')


class LeaveAddApiTest(TestCase):
    """後台月曆拖曳 → 新增／修改排休或請假"""

    def setUp(self):
        User.objects.create_user(username='boss_lv', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_lv', password='pass12345')
        u = User.objects.create_user(username='lv1', password='x', first_name='甲', last_name='陳')
        self.emp = Employee.objects.create(user=u, employee_id='LV1', department='外送')

    def _post(self, payload):
        import json as _json
        return self.client.post('/dashboard/leave/api/add/', data=_json.dumps(payload),
                                content_type='application/json')

    def test_add_rest(self):
        resp = self._post({'employee_id': self.emp.pk, 'date': '2026-09-10'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['kind'], 'rest')
        self.assertEqual(data['label'], '休')
        lr = LeaveRecord.objects.get(pk=data['id'])
        self.assertIsNone(lr.hours)
        self.assertEqual(lr.leave_type, '')

    def test_add_partial_leave(self):
        resp = self._post({'employee_id': self.emp.pk, 'date': '2026-09-11',
                           'kind': 'leave', 'hours': 2, 'leave_type': 'sick'})
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['label'], '2h')
        lr = LeaveRecord.objects.get(pk=data['id'])
        self.assertEqual(lr.hours, 2)
        self.assertEqual(lr.leave_type, 'sick')

    def test_leave_requires_hours(self):
        resp = self._post({'employee_id': self.emp.pk, 'date': '2026-09-12', 'kind': 'leave'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_resubmit_updates_same_day(self):
        """同一員工同一天再送一次 = 老闆修改，不會重複建立"""
        first = self._post({'employee_id': self.emp.pk, 'date': '2026-09-13'}).json()
        second = self._post({'employee_id': self.emp.pk, 'date': '2026-09-13',
                             'kind': 'leave', 'hours': 4, 'leave_type': 'annual'}).json()
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(LeaveRecord.objects.filter(employee=self.emp,
                                                    date=date(2026, 9, 13)).count(), 1)
        lr = LeaveRecord.objects.get(pk=second['id'])
        self.assertEqual(lr.kind, LeaveRecord.KIND_LEAVE)
        self.assertEqual(lr.hours, 4)

    def test_hours_capped_at_full_day(self):
        data = self._post({'employee_id': self.emp.pk, 'date': '2026-09-14',
                           'kind': 'leave', 'hours': 12}).json()
        self.assertEqual(LeaveRecord.objects.get(pk=data['id']).hours, 8)

    def test_bad_kind_rejected(self):
        resp = self._post({'employee_id': self.emp.pk, 'date': '2026-09-15', 'kind': 'nope'})
        self.assertEqual(resp.status_code, 400)


class ReportLeaveDisplayTest(TestCase):
    """出勤報表要顯示休假／請假，且不再算成缺勤"""

    def setUp(self):
        User.objects.create_user(username='boss_rp', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_rp', password='pass12345')
        u = User.objects.create_user(username='rp1', password='x', first_name='乙', last_name='林')
        self.emp = Employee.objects.create(user=u, employee_id='RP1', department='外送')
        # 2026/9：1(二) 排休、2(三) 整天請假、3(四) 請假 2 小時
        LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, 1))
        LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, 2),
                                   kind=LeaveRecord.KIND_LEAVE, leave_type='sick', hours=8)
        LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, 3),
                                   kind=LeaveRecord.KIND_LEAVE, leave_type='personal', hours=2)

    def _days(self):
        resp = self.client.get(f'/reports/?employee_id={self.emp.pk}&year=2026&month=9')
        self.assertEqual(resp.status_code, 200)
        return resp, {d['date'].day: d for d in resp.context['month_data']}

    def test_statuses(self):
        _, days = self._days()
        self.assertEqual(days[1]['status'], 'rest')
        self.assertEqual(days[2]['status'], 'leave')
        # 部分時數請假、當天沒打卡 → 仍是缺勤
        self.assertEqual(days[3]['status'], 'absent')
        self.assertEqual(days[3]['leave_hours'], 2)
        self.assertFalse(days[3]['leave_is_full'])

    def test_stats_count_leave_not_absent(self):
        resp, _ = self._days()
        stats = resp.context['stats']
        self.assertEqual(stats['rest'], 1)
        self.assertEqual(stats['leave'], 1)
        self.assertEqual(stats['off_total'], 2)
        self.assertEqual(stats['partial_leave_hours'], 2)

    def test_page_shows_leave_labels(self):
        resp, _ = self._days()
        html = resp.content.decode()
        self.assertIn('休假', html)
        self.assertIn('請假', html)
        # 隱私：報表不顯示假別與原因
        self.assertNotIn('病假', html)
        self.assertNotIn('事假', html)

    def test_csv_export_has_leave_columns(self):
        resp = self.client.get('/reports/export/csv/'
                               f'?employee_id={self.emp.pk}&year_from=2026&month_from=9'
                               '&year_to=2026&month_to=9')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8-sig')
        self.assertIn('請假時數', body)


class LeaveFullDayComplianceTest(TestCase):
    """一例一休只採計整天休假"""

    def setUp(self):
        User.objects.create_user(username='boss_fc', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_fc', password='pass12345')
        u = User.objects.create_user(username='fc1', password='x', first_name='丙', last_name='吳')
        self.emp = Employee.objects.create(user=u, employee_id='FC1', department='外送',
                                           work_days='0,1,2,3,4,5')

    def test_partial_leave_does_not_satisfy_weekly_rest(self):
        # 2026/9 整個月每週只請 2 小時 → 都不算有休到
        for d in (1, 8, 15, 22, 29):
            LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, d),
                                       kind=LeaveRecord.KIND_LEAVE, hours=2)
        resp = self.client.get('/dashboard/leave/?year=2026&month=9')
        row = {r['employee'].pk: r for r in resp.context['week_compliance']}[self.emp.pk]
        self.assertEqual(row['miss_count'], len(row['cells']))

    def test_full_day_leave_satisfies_weekly_rest(self):
        for d in (1, 8, 15, 22, 29):
            LeaveRecord.objects.create(employee=self.emp, date=date(2026, 9, d),
                                       kind=LeaveRecord.KIND_LEAVE, hours=8)
        resp = self.client.get('/dashboard/leave/?year=2026&month=9')
        row = {r['employee'].pk: r for r in resp.context['week_compliance']}[self.emp.pk]
        # 9/1, 9/8, 9/15, 9/22, 9/29 皆為週二 → 涵蓋的各週都有整天休
        self.assertLess(row['miss_count'], len(row['cells']))


class LineLeaveFlowTest(TestCase):
    """LINE 員工端：排休與請假是兩個入口，全程按鈕、不需打字"""

    def setUp(self):
        from attendance.models import LeaveRequest
        cache.clear()
        self.LeaveRequest = LeaveRequest
        u = User.objects.create_user(username='ln1', password='x', first_name='丁', last_name='黃')
        self.emp = Employee.objects.create(user=u, employee_id='LN1', department='外送',
                                           line_user_id='U_line_1')
        self.uid = 'U_line_1'

    def _pb(self, action, params=None, postback_params=None):
        from attendance import line_leave
        return line_leave.handle_postback(
            self.emp, self.uid, action, params or {}, postback_params or {})

    # ── 排休 ──────────────────────────────────────────
    def test_rest_flow_collects_multiple_days(self):
        from attendance import line_leave
        line_leave.start_rest(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-05'})
        self._pb('lv_date', postback_params={'date': '2026-10-06'})
        self._pb('lv_submit')

        req = self.LeaveRequest.objects.get(employee=self.emp)
        self.assertEqual(req.kind, LeaveRecord.KIND_REST)
        self.assertEqual(req.dates, ['2026-10-05', '2026-10-06'])
        self.assertIsNone(req.hours)
        self.assertEqual(req.leave_type, '')

    def test_rest_rejects_duplicate_date(self):
        from attendance import line_leave
        line_leave.start_rest(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-05'})
        self._pb('lv_date', postback_params={'date': '2026-10-05'})
        self._pb('lv_submit')
        self.assertEqual(self.LeaveRequest.objects.get(employee=self.emp).dates, ['2026-10-05'])

    def test_submit_without_date_creates_nothing(self):
        from attendance import line_leave
        line_leave.start_rest(self.uid)
        self._pb('lv_submit')
        self.assertFalse(self.LeaveRequest.objects.exists())

    # ── 請假 ──────────────────────────────────────────
    def test_leave_flow_date_hours_type(self):
        from attendance import line_leave
        line_leave.start_leave(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-07'})
        self._pb('lv_hours', {'h': '2'})
        self._pb('lv_type', {'t': 'sick'})
        self._pb('lv_submit')

        req = self.LeaveRequest.objects.get(employee=self.emp)
        self.assertEqual(req.kind, LeaveRecord.KIND_LEAVE)
        self.assertEqual(req.dates, ['2026-10-07'])
        self.assertEqual(req.hours, 2)
        self.assertEqual(req.leave_type, 'sick')
        self.assertEqual(req.hours_label, '2 小時')

    def test_leave_is_single_day(self):
        """請假有時數，一次只處理一天；再選日期是換日期不是加天"""
        from attendance import line_leave
        line_leave.start_leave(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-07'})
        self._pb('lv_date', postback_params={'date': '2026-10-08'})
        self._pb('lv_hours', {'h': '8'})
        self._pb('lv_type', {'t': 'personal'})
        self._pb('lv_submit')
        self.assertEqual(self.LeaveRequest.objects.get(employee=self.emp).dates, ['2026-10-08'])

    def test_employee_cannot_pick_annual_leave(self):
        """特休要跟老闆談，員工端不開放選"""
        from attendance import line_leave
        line_leave.start_leave(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-09'})
        self._pb('lv_hours', {'h': '8'})
        self._pb('lv_type', {'t': 'annual'})
        self._pb('lv_submit')
        req = self.LeaveRequest.objects.get(employee=self.emp)
        self.assertEqual(req.leave_type, '')   # 沒被設進去

    # ── 共通 ──────────────────────────────────────────
    def test_cancel_clears_draft(self):
        from attendance import line_leave
        line_leave.start_rest(self.uid)
        self._pb('lv_date', postback_params={'date': '2026-10-05'})
        self._pb('lv_cancel')
        self._pb('lv_submit')
        self.assertFalse(self.LeaveRequest.objects.exists())

    def test_expired_draft_is_handled(self):
        msgs = self._pb('lv_date', postback_params={'date': '2026-10-05'})
        self.assertTrue(msgs)
        self.assertFalse(self.LeaveRequest.objects.exists())

    def test_non_leave_action_passes_through(self):
        self.assertIsNone(self._pb('query'))

    def test_text_entries_are_separate(self):
        from attendance.views import _process_message
        self.assertTrue(_process_message('排休', self.uid))
        from attendance import line_leave
        self.assertEqual(line_leave._get_draft(self.uid)['kind'], LeaveRecord.KIND_REST)
        self.assertTrue(_process_message('請假', self.uid))
        self.assertEqual(line_leave._get_draft(self.uid)['kind'], LeaveRecord.KIND_LEAVE)


class LeaveRequestApprovalTest(TestCase):
    """核准申請要依類別寫成正確的休假紀錄"""

    def setUp(self):
        from attendance.models import LeaveRequest
        self.LeaveRequest = LeaveRequest
        User.objects.create_user(username='boss_ap', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_ap', password='pass12345')
        u = User.objects.create_user(username='ap1', password='x', first_name='戊', last_name='張')
        self.emp = Employee.objects.create(user=u, employee_id='AP1', department='外送')

    def test_approve_rest_creates_rest_records(self):
        req = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-05', '2026-10-06'],
            kind=LeaveRecord.KIND_REST,
        )
        resp = self.client.post(f'/dashboard/leave/requests/{req.pk}/approve/')
        self.assertEqual(resp.status_code, 302)
        records = LeaveRecord.objects.filter(employee=self.emp).order_by('date')
        self.assertEqual(records.count(), 2)
        self.assertTrue(all(r.is_rest and r.is_full_day for r in records))

    def test_approve_leave_carries_hours_and_type(self):
        req = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-07'],
            kind=LeaveRecord.KIND_LEAVE, leave_type='sick', hours=2,
        )
        self.client.post(f'/dashboard/leave/requests/{req.pk}/approve/')
        lr = LeaveRecord.objects.get(employee=self.emp, date=date(2026, 10, 7))
        self.assertEqual(lr.kind, LeaveRecord.KIND_LEAVE)
        self.assertEqual(lr.hours, 2)
        self.assertEqual(lr.leave_type, 'sick')
        self.assertFalse(lr.is_full_day)

    def test_deny_creates_no_records(self):
        req = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-08'], kind=LeaveRecord.KIND_REST,
        )
        self.client.post(f'/dashboard/leave/requests/{req.pk}/deny/')
        req.refresh_from_db()
        self.assertEqual(req.status, 'denied')
        self.assertFalse(LeaveRecord.objects.filter(employee=self.emp).exists())

    def test_approve_twice_is_idempotent(self):
        req = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-09'], kind=LeaveRecord.KIND_REST,
        )
        self.client.post(f'/dashboard/leave/requests/{req.pk}/approve/')
        self.client.post(f'/dashboard/leave/requests/{req.pk}/approve/')
        self.assertEqual(LeaveRecord.objects.filter(employee=self.emp).count(), 1)

    def test_summary_label(self):
        rest = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-10'], kind=LeaveRecord.KIND_REST)
        self.assertEqual(rest.summary_label, '排休・整天')
        leave = self.LeaveRequest.objects.create(
            employee=self.emp, dates=['2026-10-11'],
            kind=LeaveRecord.KIND_LEAVE, leave_type='personal', hours=4)
        self.assertEqual(leave.summary_label, '請假・半天・事假')


class HolidayCalendarDisplayTest(TestCase):
    """國定假日要在日曆見紅，且不算缺勤"""

    def setUp(self):
        from attendance.models import Holiday
        self.Holiday = Holiday
        User.objects.create_user(username='boss_hd', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_hd', password='pass12345')
        u = User.objects.create_user(username='hd1', password='x', first_name='己', last_name='蔡')
        self.emp = Employee.objects.create(user=u, employee_id='HD1', department='外送',
                                           work_days='0,1,2,3,4,5')
        # 2026/10/10（六）國慶日、10/9（五）補假
        Holiday.objects.create(date=date(2026, 10, 9), name='國慶日補假')
        Holiday.objects.create(date=date(2026, 10, 10), name='國慶日')

    def _days(self):
        resp = self.client.get(f'/reports/?employee_id={self.emp.pk}&year=2026&month=10')
        self.assertEqual(resp.status_code, 200)
        return resp, {d['date'].day: d for d in resp.context['month_data']}

    def test_holiday_is_not_absent(self):
        _, days = self._days()
        self.assertEqual(days[9]['status'], 'holiday')
        self.assertTrue(days[9]['is_holiday'])
        self.assertEqual(days[9]['holiday_name'], '國慶日補假')
        self.assertFalse(days[9]['holiday_worked'])

    def test_non_holiday_weekday_still_absent(self):
        _, days = self._days()
        self.assertEqual(days[8]['status'], 'absent')
        self.assertFalse(days[8]['is_holiday'])

    def test_holiday_worked_is_flagged(self):
        AttendanceRecord.objects.create(
            employee=self.emp, record_type='clock_in',
            timestamp=timezone.make_aware(datetime(2026, 10, 10, 9, 0)),
            latitude=0, longitude=0, is_valid=True, distance_meters=0)
        AttendanceRecord.objects.create(
            employee=self.emp, record_type='clock_out',
            timestamp=timezone.make_aware(datetime(2026, 10, 10, 18, 0)),
            latitude=0, longitude=0, is_valid=True, distance_meters=0)
        resp, days = self._days()
        self.assertTrue(days[10]['holiday_worked'])
        self.assertEqual(days[10]['status'], 'normal')   # 有打卡仍是正常出勤
        self.assertEqual(resp.context['stats']['holiday_worked'], 1)

    def test_holiday_does_not_count_as_absent_in_stats(self):
        resp, _ = self._days()
        stats = resp.context['stats']
        self.assertEqual(stats['holiday'], 2)
        # 10/9、10/10 不應被算進缺勤
        self.assertEqual(stats['absent'],
                         sum(1 for d in resp.context['month_data'] if d['status'] == 'absent'))
        self.assertNotIn('holiday', [d['status'] for d in resp.context['month_data']
                                     if d['status'] == 'absent'])

    def test_report_page_shows_holiday_name(self):
        resp, _ = self._days()
        self.assertIn('國慶日', resp.content.decode())

    def test_csv_has_holiday_column(self):
        resp = self.client.get('/reports/export/csv/'
                               f'?employee_id={self.emp.pk}&year_from=2026&month_from=10'
                               '&year_to=2026&month_to=10')
        body = resp.content.decode('utf-8-sig')
        self.assertIn('國定假日', body)

    def test_leave_calendar_has_holidays(self):
        resp = self.client.get('/dashboard/leave/?year=2026&month=10')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['holiday_by_day'][10], '國慶日')
        self.assertIn('國慶日', resp.content.decode())

    def test_holiday_without_name_falls_back(self):
        self.Holiday.objects.create(date=date(2026, 10, 20))
        _, days = self._days()
        self.assertEqual(days[20]['holiday_name'], '國定假日')

    def test_leave_on_holiday_still_shows_red(self):
        """國定假日當天有排休 → 狀態仍是休假，但日曆要見紅"""
        LeaveRecord.objects.create(employee=self.emp, date=date(2026, 10, 9))
        _, days = self._days()
        self.assertEqual(days[9]['status'], 'rest')
        self.assertTrue(days[9]['is_holiday'])


class DictGetFilterTest(TestCase):
    def test_dict_get(self):
        from attendance.templatetags.attendance_extras import dict_get
        self.assertEqual(dict_get({1: 'a'}, 1), 'a')
        self.assertIsNone(dict_get({1: 'a'}, 2))
        self.assertIsNone(dict_get(None, 1))


class HourlyMinuteBasedPayrollTest(TestCase):
    """時薪制改為分鐘制：工時不進位，金額逐日四捨五入"""

    def setUp(self):
        from datetime import time as _time
        self.emp = Employee.objects.create(
            user=User.objects.create_user(username='mp1', password='x',
                                          first_name='庚', last_name='許'),
            employee_id='MP1', department='外送',
            employment_type='hourly', hourly_rate=200,
            work_start_time=_time(9, 0), work_days='0,1,2,3,4,5',
        )

    def _punch(self, d, in_hm, out_hm, break_hm=None):
        def mk(kind, hm):
            AttendanceRecord.objects.create(
                employee=self.emp, record_type=kind,
                timestamp=timezone.make_aware(datetime(d.year, d.month, d.day, *hm)),
                latitude=0, longitude=0, is_valid=True, distance_meters=0)
        mk('clock_in', in_hm)
        if break_hm:
            mk('break_start', break_hm[0])
            mk('break_end', break_hm[1])
        mk('clock_out', out_hm)

    def _minutes(self, d):
        from attendance.dashboard_views.base import get_work_minutes
        return get_work_minutes(self.emp, d)

    # ── 工時不再進位 ──────────────────────────────────
    def test_no_half_hour_rounding_on_clock_out(self):
        """9:00–17:50 → 530 分，不會被進位成 9 小時"""
        d = date(2026, 11, 3)   # 週二
        self._punch(d, (9, 0), (17, 50))
        self.assertEqual(self._minutes(d), 530)

    def test_odd_minutes_are_kept(self):
        d = date(2026, 11, 4)
        self._punch(d, (9, 0), (17, 7))
        self.assertEqual(self._minutes(d), 487)

    def test_break_is_deducted_by_minute(self):
        d = date(2026, 11, 5)
        self._punch(d, (9, 0), (18, 0), break_hm=[(12, 10), (12, 55)])
        self.assertEqual(self._minutes(d), 540 - 45)

    # ── 早到 / 遲到 ───────────────────────────────────
    def test_early_arrival_does_not_add_time(self):
        """8:30 到，仍從排班 9:00 起算"""
        d = date(2026, 11, 6)
        self._punch(d, (8, 30), (18, 0))
        self.assertEqual(self._minutes(d), 540)

    def test_late_within_grace_starts_from_schedule(self):
        """9:08 到（寬限內）→ 仍從 9:00 起算，不扣"""
        d = date(2026, 11, 9)
        self._punch(d, (9, 8), (18, 0))
        self.assertEqual(self._minutes(d), 540)

    def test_late_beyond_grace_starts_from_actual(self):
        """9:20 到 → 從 9:20 起算，只少 20 分，不是罰半小時"""
        d = date(2026, 11, 10)
        self._punch(d, (9, 20), (18, 0))
        self.assertEqual(self._minutes(d), 520)

    def test_late_minutes_reported(self):
        from attendance.dashboard_views.base import get_late_minutes
        d = date(2026, 11, 11)
        self._punch(d, (9, 20), (18, 0))
        self.assertEqual(get_late_minutes(self.emp, d), 20)
        self.assertEqual(get_late_minutes(self.emp, date(2026, 11, 12)), 0)

    def test_late_within_grace_is_not_counted_as_late(self):
        from attendance.dashboard_views.base import get_late_minutes
        d = date(2026, 11, 13)
        self._punch(d, (9, 8), (18, 0))
        self.assertEqual(get_late_minutes(self.emp, d), 0)

    # ── 金額 ─────────────────────────────────────────
    def test_amount_rounds_per_day(self):
        """9:00–17:50 = 530 分。前 8h 為底薪，超過的 50 分是加班（×4/3）。"""
        d = date(2026, 11, 3)
        self._punch(d, (9, 0), (17, 50))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        self.assertEqual(len(work['detail']), 1)
        row = work['detail'][0]
        self.assertEqual(row['minutes'], 530)
        self.assertEqual(row['base_amount'], 8 * 200)
        self.assertEqual(row['ot_amount'], round(200 * (50 / 60) * 4 / 3))
        self.assertEqual(row['amount'], row['base_amount'] + row['ot_amount'])

    def test_short_day_is_all_base(self):
        """未滿 8 小時全部算底薪，金額四捨五入到元：287 分 × 200/60 = 956.67 → 957"""
        d = date(2026, 11, 4)
        self._punch(d, (9, 0), (13, 47))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        row = work['detail'][0]
        self.assertEqual(row['minutes'], 287)
        self.assertEqual(row['ot_amount'], 0)
        self.assertEqual(row['base_amount'], 957)

    def test_detail_sums_to_total(self):
        """明細逐日相加要剛好等於底薪與加班費總額"""
        self._punch(date(2026, 11, 3), (9, 0), (17, 50))
        self._punch(date(2026, 11, 4), (9, 0), (17, 7))
        self._punch(date(2026, 11, 5), (9, 0), (19, 23))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        self.assertEqual(sum(x['base_amount'] for x in work['detail']), work['base'])
        self.assertEqual(sum(x['ot_amount'] for x in work['detail']), work['overtime'])

    def test_overtime_uses_minutes(self):
        """9:00–19:30 = 630 分 = 10.5h → 平日加班 2.5h（前 2h ×4/3、0.5h ×5/3）"""
        d = date(2026, 11, 3)
        self._punch(d, (9, 0), (19, 30))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        row = work['detail'][0]
        self.assertEqual(row['minutes'], 630)
        self.assertEqual(row['normal_hours'], 8.0)
        self.assertEqual(row['ot_hours'], 2.5)
        expected = round(200 * (2 * 4 / 3 + 0.5 * 5 / 3))
        self.assertEqual(row['ot_amount'], expected)
        self.assertAlmostEqual(work['tiers']['weekday_1_2'], 2.0)
        self.assertAlmostEqual(work['tiers']['weekday_3plus'], 0.5)

    def test_salary_total_matches_parts(self):
        from attendance.dashboard_views.base import calculate_salary
        self._punch(date(2026, 11, 3), (9, 0), (17, 50))
        self._punch(date(2026, 11, 4), (9, 20), (18, 0))
        r = calculate_salary(self.emp, 2026, 11)
        self.assertEqual(
            r['total'],
            r['base'] + r['maintenance'] + r['allowance'] + r['overtime'] - r['deduction'])
        self.assertEqual(r['late_days'], 1)
        self.assertEqual(r['late_minutes'], 20)

    def test_late_does_not_reduce_pay_beyond_missing_time(self):
        """遲到 20 分只少 20 分鐘的錢，沒有額外懲罰"""
        on_time = date(2026, 11, 3)
        self._punch(on_time, (9, 0), (18, 0))
        w = payroll.monthly_work_detail(self.emp, 2026, 11)
        pay_on_time = w['base'] + w['overtime']

        AttendanceRecord.objects.all().delete()
        late = date(2026, 11, 4)
        self._punch(late, (9, 20), (18, 0))
        w = payroll.monthly_work_detail(self.emp, 2026, 11)
        pay_late = w['base'] + w['overtime']

        # 少的 20 分鐘落在第 9 小時（加班區），所以以 4/3 計，且沒有額外懲罰
        self.assertEqual(pay_on_time - pay_late, round(200 * (20 / 60) * 4 / 3))

    def test_fmt_hm(self):
        self.assertEqual(payroll.fmt_hm(530), '8小時50分')
        self.assertEqual(payroll.fmt_hm(480), '8小時')
        self.assertEqual(payroll.fmt_hm(45), '45分')

    def test_maintenance_uses_minutes_threshold(self):
        from attendance.dashboard_views.base import calculate_salary
        self._punch(date(2026, 11, 3), (9, 0), (13, 0))    # 240 分 → 100
        self._punch(date(2026, 11, 4), (9, 0), (12, 30))   # 210 分 → 50
        r = calculate_salary(self.emp, 2026, 11)
        self.assertEqual(r['maintenance'], 150)

    def test_salary_detail_page_renders(self):
        User.objects.create_user(username='boss_mp', password='pass12345',
                                 is_staff=True, is_superuser=True)
        self.client.login(username='boss_mp', password='pass12345')
        self._punch(date(2026, 11, 3), (9, 0), (17, 50))
        resp = self.client.get(f'/dashboard/salary/{self.emp.pk}/detail/?year=2026&month=11')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('day_detail', resp.context)
        self.assertIn('8小時50分', resp.content.decode())


class ClockOutGraceTest(TestCase):
    """下班後 10 分鐘內收尾不算加班"""

    def setUp(self):
        from datetime import time as _time
        self.emp = Employee.objects.create(
            user=User.objects.create_user(username='og1', password='x',
                                          first_name='辛', last_name='鄭'),
            employee_id='OG1', department='外送',
            employment_type='hourly', hourly_rate=200,
            work_start_time=_time(9, 0), work_end_time=_time(18, 0),
            work_days='0,1,2,3,4,5',
        )

    def _punch(self, d, in_hm, out_hm):
        for kind, hm in (('clock_in', in_hm), ('clock_out', out_hm)):
            AttendanceRecord.objects.create(
                employee=self.emp, record_type=kind,
                timestamp=timezone.make_aware(datetime(d.year, d.month, d.day, *hm)),
                latitude=0, longitude=0, is_valid=True, distance_meters=0)

    def _minutes(self, d):
        from attendance.dashboard_views.base import get_work_minutes
        return get_work_minutes(self.emp, d)

    def test_within_grace_counts_to_scheduled_end(self):
        """18:07 下班 → 算到 18:00，不多給 7 分鐘"""
        d = date(2026, 11, 3)
        self._punch(d, (9, 0), (18, 7))
        self.assertEqual(self._minutes(d), 540)

    def test_exactly_at_grace_edge(self):
        """18:10 剛好在寬限內"""
        d = date(2026, 11, 4)
        self._punch(d, (9, 0), (18, 10))
        self.assertEqual(self._minutes(d), 540)

    def test_beyond_grace_counts_fully(self):
        """18:25 → 超過寬限，25 分鐘全部照算（不是只算超出寬限的 15 分）"""
        d = date(2026, 11, 5)
        self._punch(d, (9, 0), (18, 25))
        self.assertEqual(self._minutes(d), 565)

    def test_early_leave_is_not_padded(self):
        """17:40 早退 → 照實際算，寬限不會把時間補回去"""
        d = date(2026, 11, 6)
        self._punch(d, (9, 0), (17, 40))
        self.assertEqual(self._minutes(d), 520)

    def test_grace_removes_trivial_overtime(self):
        """8 小時班（9:00–17:00）拖到 17:07 下班，不會因此產生加班費"""
        from datetime import time as _time
        self.emp.work_end_time = _time(17, 0)
        self.emp.save()
        d = date(2026, 11, 3)
        self._punch(d, (9, 0), (17, 7))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        self.assertEqual(work['detail'][0]['minutes'], 480)
        self.assertEqual(work['overtime'], 0)
        self.assertEqual(work['detail'][0]['ot_hours'], 0)

    def test_beyond_grace_does_produce_overtime(self):
        """同樣是 8 小時班，拖到 17:25 就確實有加班費"""
        from datetime import time as _time
        self.emp.work_end_time = _time(17, 0)
        self.emp.save()
        d = date(2026, 11, 4)
        self._punch(d, (9, 0), (17, 25))
        work = payroll.monthly_work_detail(self.emp, 2026, 11)
        self.assertEqual(work['detail'][0]['minutes'], 505)
        self.assertEqual(work['overtime'], round(200 * (25 / 60) * 4 / 3))

    def test_no_work_end_time_means_no_grace(self):
        """沒設下班時間的員工不套用寬限"""
        self.emp.work_end_time = None
        self.emp.save()
        d = date(2026, 11, 3)
        self._punch(d, (9, 0), (18, 7))
        self.assertEqual(self._minutes(d), 547)
