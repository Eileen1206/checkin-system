from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.cache import cache
from attendance.models import (
    Employee, Customer, AttendanceRecord, DeliverySession, DeliveryTask, LeaveRecord,
)
from django.utils import timezone
from datetime import date, timedelta
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
