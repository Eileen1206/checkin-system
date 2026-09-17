from django.core.exceptions import PermissionDenied
from django.utils import timezone
from datetime import datetime
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
    employees = Employee.tracked.select_related('user').all()
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


LATE_GRACE_SECONDS = 600         # 遲到寬限 10 分鐘（寬限內從排班上班時間起算）
OVERTIME_GRACE_SECONDS = 600     # 下班寬限 10 分鐘（寬限內算到排班下班時間，不算加班）


def get_work_minutes(employee, date=None):
    """回傳某天的計薪分鐘數（整數分鐘，不做任何半小時進位）。

    計薪區間：
    - 起點：排班上班時間與實際打卡取「晚」的那個。
      早到不因此多算；遲到超過寬限則從實際打卡起算（沒做就沒錢，但不額外罰）。
    - 終點：實際下班打卡時間，但排班下班後 10 分鐘內算到排班時間（收個尾不算加班）；
      超過寬限才照實際打卡算。今天還沒下班用現在時間估；過去日期缺下班卡視為異常，不計。
    - 中間扣掉落在計薪區間內的午休。
    """
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
        end_time = clock_out.timestamp
        if employee.work_end_time:
            co_local = clock_out.timestamp.astimezone()
            scheduled_naive = datetime.combine(co_local.date(), employee.work_end_time)
            co_naive = datetime.combine(co_local.date(), co_local.time())
            over_seconds = (co_naive - scheduled_naive).total_seconds()
            if 0 < over_seconds <= OVERTIME_GRACE_SECONDS:
                # 下班後 10 分鐘內收個尾 → 算到排班下班時間，不算加班
                end_time = clock_out.timestamp + (scheduled_naive - co_naive)
    elif date == timezone.localdate():
        end_time = timezone.now()
    else:
        return 0

    # 起算時間
    start_time = clock_in.timestamp
    if employee.work_start_time:
        ci_local = clock_in.timestamp.astimezone()
        scheduled_naive = datetime.combine(ci_local.date(), employee.work_start_time)
        ci_naive = datetime.combine(ci_local.date(), ci_local.time())
        late_seconds = (ci_naive - scheduled_naive).total_seconds()
        if late_seconds <= LATE_GRACE_SECONDS:
            # 早到或寬限內遲到 → 從排班時間起算
            start_time = clock_in.timestamp + (scheduled_naive - ci_naive)

    total_seconds = max((end_time - start_time).total_seconds(), 0)

    # 扣除午休（只扣落在計薪區間內的部分）
    break_start = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='break_start'
    ).first()
    if break_start:
        break_end = AttendanceRecord.objects.filter(
            employee=employee, timestamp__date=date, record_type='break_end'
        ).first()
        eff_start = max(break_start.timestamp, start_time)
        eff_end = min(break_end.timestamp if break_end else end_time, end_time)
        if eff_end > eff_start:
            total_seconds -= (eff_end - eff_start).total_seconds()
        total_seconds = max(total_seconds, 0)

    return int(total_seconds // 60)


def get_work_hours(employee, date=None):
    """計薪工時（小時）。以分鐘為精度，不做半小時進位。"""
    return round(get_work_minutes(employee, date) / 60, 2)


def get_late_minutes(employee, date=None):
    """回傳當天遲到分鐘數（未超過寬限回 0）。遲到不扣薪，只供報表顯示。"""
    date = date or timezone.localdate()
    if not employee.work_start_time:
        return 0

    clock_in = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=date, record_type='clock_in'
    ).first()
    if clock_in is None:
        return 0

    ci_local = clock_in.timestamp.astimezone()
    scheduled = datetime.combine(ci_local.date(), employee.work_start_time)
    actual = datetime.combine(ci_local.date(), ci_local.time())
    late_seconds = (actual - scheduled).total_seconds()
    if late_seconds <= LATE_GRACE_SECONDS:
        return 0
    return int(late_seconds // 60)


WORK_DAY_CHOICES = [
    (0, '週一'), (1, '週二'), (2, '週三'), (3, '週四'),
    (4, '週五'), (5, '週六'), (6, '週日'),
]


MAINTENANCE_FULL = 100       # 當天工時達 4 小時的保養費
MAINTENANCE_HALF = 50
MAINTENANCE_THRESHOLD_MIN = 240


def calculate_salary(emp, year, month):
    """當月薪資。

    時薪制：工時以分鐘計，每日金額四捨五入到元後加總，
    因此明細逐日相加會剛好等於底薪與加班費總額。
    遲到不扣薪，只回傳次數與分鐘供報表顯示。
    """
    allowance = MonthlyAllowance.objects.filter(
        employee=emp, year=year, month=month
    ).first()
    allowance_amount = float(allowance.amount) if allowance else 0

    from attendance.utils import payroll
    work = payroll.monthly_work_detail(emp, year, month)
    overtime = work['overtime']

    if emp.employment_type == 'monthly':
        base = float(emp.monthly_salary) if emp.monthly_salary else 0
        maintenance = 0
        deduction = 0
    else:
        # 底薪只計「正常工時」，加班時數由加班費（全額）計算，避免重複
        base = work['base']
        maintenance = sum(
            MAINTENANCE_FULL if x['minutes'] >= MAINTENANCE_THRESHOLD_MIN else MAINTENANCE_HALF
            for x in work['detail']
        )
        labor = float(emp.labor_insurance_amount) if emp.labor_insurance_amount else 0
        health = float(emp.health_insurance_amount) if emp.health_insurance_amount else 0
        deduction = labor + health

    total = base + maintenance + allowance_amount + overtime - deduction
    return {
        'employee': emp,
        'base': base,
        'normal_hours': work['normal_hours'],
        'maintenance': maintenance,
        'allowance': allowance_amount,
        'overtime': overtime,
        'overtime_detail': [
            {'date': x['date'], 'cls': x['cls'], 'hours': x['hours'],
             'hm': x['hm'], 'amount': x['ot_amount']}
            for x in work['detail'] if x['ot_amount'] > 0
        ],
        'overtime_tiers': work['tiers'],
        'deduction': deduction,
        'total': total,
        # 工時與遲到（遲到不影響金額）
        'day_detail':    work['detail'],
        'work_minutes':  work['work_minutes'],
        'work_hm':       work['work_hm'],
        'late_days':     work['late_days'],
        'late_minutes':  work['late_minutes'],
        'late_hm':       work['late_hm'],
    }
