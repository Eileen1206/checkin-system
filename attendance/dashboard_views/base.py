from django.core.exceptions import PermissionDenied
from django.utils import timezone
from datetime import datetime, date as date_type
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

    # 查每位員工今天的最後一筆打卡
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

    end_time = clock_out.timestamp if clock_out else timezone.now()
    duration = end_time - clock_in.timestamp
    total_seconds = duration.total_seconds()

    # 工時進位：以半小時為單位，15分(900秒)以上進半小時
    half_hours = total_seconds // 1800       # 完整的半小時數
    remainder  = total_seconds % 1800        # 不足半小時的秒數
    if remainder >= 900:
        half_hours += 1
    hours = half_hours / 2                   # 換算回小時

    # 遲到扣薪：比上班時間晚超過10分鐘 → 扣0.5小時
    if employee.work_start_time:
        clock_in_time = clock_in.timestamp.astimezone().time()
        clock_in_date = clock_in.timestamp.astimezone().date()
        # 計算遲到幾分鐘
        scheduled = datetime.combine(clock_in_date, employee.work_start_time)
        actual    = datetime.combine(date_type.today(), clock_in_time)
        late_minutes = (actual - scheduled).total_seconds() / 60  # 換算成分鐘
        if late_minutes > 10:
            hours -= 0.5

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
        base = float(emp.monthly_salary or 0)
        maintenance = 0
        deduction = 0
    else:
        total_hours = sum(
            get_work_hours(emp, d)
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        base = total_hours * float(emp.hourly_rate or 0)
        maintenance = sum(
            100 if get_work_hours(emp, d) >= 4 else 50
            for d in records.filter(record_type='clock_in').dates('timestamp', 'day')
        )
        deduction = float(emp.labor_insurance_amount or 0) + \
                    float(emp.health_insurance_amount or 0)

    total = base + maintenance + allowance_amount - deduction
    return {
        'employee': emp,
        'base': base,
        'maintenance': maintenance,
        'allowance': allowance_amount,
        'deduction': deduction,
        'total': total,
    }
