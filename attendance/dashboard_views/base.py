from django.core.exceptions import PermissionDenied
from django.utils import timezone
from datetime import datetime, timedelta
import math
from ..models import Employee, AttendanceRecord, MonthlyAllowance


def require_group(*group_names):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if request.user.groups.filter(name__in=group_names).exists() or request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied
        return wrapper
    return decorator


def get_today_status():
    """
    回傳今日所有員工的出勤狀態。
    回傳格式：{employee: status_string}
    status 值：'absent' | 'working' | 'break' | 'left'
    """
    employees = Employee.objects.select_related('user').all()
    today = timezone.localdate()
    status_map = {}

    for emp in employees:
        last = AttendanceRecord.objects.filter(
            employee=emp,
            timestamp__date=today
        ).first()

        if last is None:
            status_map[emp] = 'absent'
        elif last.record_type in ('clock_in', 'break_end'):
            status_map[emp] = 'working'
        elif last.record_type == 'break_start':
            status_map[emp] = 'break'
        else:
            status_map[emp] = 'left'

    return status_map


def get_work_hours(employee, date=None):
    date = date or timezone.localdate()

    clock_in = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='clock_in'
    ).first()

    if clock_in is None:
        return 0

    clock_out = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='clock_out'
    ).first()

    if clock_out:
        co_local  = clock_out.timestamp.astimezone()
        co_naive  = datetime.combine(co_local.date(), co_local.time())
        # 下班時間四捨五入到最近的半小時（< 15 分捨去，≥ 15 分進位）
        total_mins = co_local.hour * 60 + co_local.minute
        remainder  = total_mins % 30
        if remainder < 15:
            rounded_mins = total_mins - remainder          # 捨去
        else:
            rounded_mins = total_mins + (30 - remainder)  # 進位
        rounded_h, rounded_m = divmod(rounded_mins, 60)
        rounded_naive = co_naive.replace(
            hour=rounded_h % 24, minute=rounded_m, second=0, microsecond=0
        )
        end_time = clock_out.timestamp + (rounded_naive - co_naive)
    else:
        end_time = timezone.now()

    # 計算計薪起始時間（考慮遲到）
    clock_in_local = clock_in.timestamp.astimezone()
    clock_in_naive = datetime.combine(clock_in_local.date(), clock_in_local.time())

    if employee.work_start_time:
        scheduled_naive = datetime.combine(clock_in_local.date(), employee.work_start_time)
        late_seconds = (clock_in_naive - scheduled_naive).total_seconds()

        if late_seconds < 0:
            # 早到 → 從實際打卡時間算（不因早到而損失工時）
            start_naive = clock_in_naive
        elif late_seconds <= 600:
            # 準時或寬限 10 分鐘內 → 從排班時間開始算
            start_naive = scheduled_naive
        else:
            # 超過 10 分鐘遲到 → 無條件進位到下一個半小時
            half_hours_late = math.ceil(late_seconds / 1800)
            start_naive = scheduled_naive + timedelta(seconds=half_hours_late * 1800)
    else:
        # 沒設排班時間 → 從實際打卡時間算
        start_naive = clock_in_naive

    # 換算回 aware datetime 做時間差計算
    start_time = clock_in.timestamp + (
        datetime.combine(clock_in_local.date(), start_naive.time()) -
        clock_in_naive
    )

    total_seconds = (end_time - start_time).total_seconds()
    if total_seconds < 0:
        total_seconds = 0

    # 扣除午休時間（只扣落在計薪區間 [start_time, end_time] 內的部分）
    break_start = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='break_start'
    ).first()
    break_end = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='break_end'
    ).first()
    if break_start:
        eff_break_start = max(break_start.timestamp, start_time)
        eff_break_end   = min(break_end.timestamp if break_end else end_time, end_time)
        if eff_break_end > eff_break_start:
            total_seconds -= (eff_break_end - eff_break_start).total_seconds()
        if total_seconds < 0:
            total_seconds = 0

    # 工時進位：以半小時為單位，15分(900秒)以上進半小時
    half_hours = total_seconds // 1800
    remainder  = total_seconds % 1800
    if remainder >= 900:
        half_hours += 1
    hours = half_hours / 2

    return hours


WORK_DAY_CHOICES = [
    (0, '週一'), (1, '週二'), (2, '週三'), (3, '週四'),
    (4, '週五'), (5, '週六'), (6, '週日'),
]


def calculate_salary(emp, year, month):
    allowance = MonthlyAllowance.objects.filter(
        employee=emp, year=year, month=month
    ).first()
    allowance_amount = float(allowance.amount) if allowance else 0

    records = AttendanceRecord.objects.filter(
        employee=emp,
        timestamp__year=year,
        timestamp__month=month
    )

    if emp.employment_type == 'monthly':
        base = float(emp.monthly_salary) if emp.monthly_salary else 0
        maintenance = 0
        deduction = 0
    else:
        total_hours = sum(
            get_work_hours(emp, d)
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        hourly = float(emp.hourly_rate) if emp.hourly_rate else 0
        base = total_hours * hourly
        maintenance = sum(
            100 if get_work_hours(emp, d) >= 4 else 50
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        labor = float(emp.labor_insurance_amount) if emp.labor_insurance_amount else 0
        health = float(emp.health_insurance_amount) if emp.health_insurance_amount else 0
        deduction = labor + health

    total = base + maintenance + allowance_amount - deduction
    return {
        'employee': emp,
        'base': base,
        'maintenance': maintenance,
        'allowance': allowance_amount,
        'deduction': deduction,
        'total': total,
    }
