import calendar
from datetime import date, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.utils.timezone import localtime

from attendance.models import AttendanceRecord, Employee


def _build_day(employee, d):
    """回傳某天的出勤資料 dict"""
    records = AttendanceRecord.objects.filter(
        employee=employee, timestamp__date=d
    ).order_by('timestamp')

    clock_in    = records.filter(record_type='clock_in').first()
    clock_out   = records.filter(record_type='clock_out').first()
    break_start = records.filter(record_type='break_start').first()
    break_end   = records.filter(record_type='break_end').first()

    is_weekend = d.weekday() >= 5

    # 判斷狀態
    if clock_in:
        is_late = False
        if employee.work_start_time:
            ci_time   = localtime(clock_in.timestamp).time()
            scheduled = datetime.combine(d, employee.work_start_time)
            actual    = datetime.combine(d, ci_time)
            is_late   = (actual - scheduled).total_seconds() > 600  # 超過10分鐘
        status = 'late' if is_late else 'normal'
    elif is_weekend:
        status = 'weekend'
    else:
        status = 'absent'

    # 計算工時（扣午休）
    hours = 0.0
    if clock_in and clock_out:
        total = (clock_out.timestamp - clock_in.timestamp).total_seconds()
        if break_start and break_end:
            total -= (break_end.timestamp - break_start.timestamp).total_seconds()
        hours = round(total / 3600, 1)

    # 打卡時間（本地時間字串）
    def fmt(record):
        return localtime(record.timestamp).strftime('%H:%M') if record else None

    return {
        'date':        d,
        'weekday':     d.weekday(),
        'is_weekend':  is_weekend,
        'clock_in':    fmt(clock_in),
        'clock_out':   fmt(clock_out),
        'break_start': fmt(break_start),
        'break_end':   fmt(break_end),
        'status':      status,
        'hours':       hours,
        'is_today':    d == timezone.localdate(),
    }


@login_required
def report(request):
    today    = timezone.localdate()
    year     = int(request.GET.get('year',  today.year))
    month    = int(request.GET.get('month', today.month))
    emp_id   = request.GET.get('employee_id')

    employees = Employee.objects.select_related('user').order_by('employee_id')

    # 選定員工
    selected = None
    if emp_id:
        selected = employees.filter(pk=emp_id).first()
    if not selected and employees.exists():
        selected = employees.first()

    month_data      = []
    calendar_weeks  = []
    stats           = {}

    if selected:
        _, days_in_month = calendar.monthrange(year, month)
        month_data = [_build_day(selected, date(year, month, d))
                      for d in range(1, days_in_month + 1)]

        # 統計
        worked_days = [d for d in month_data if d['status'] in ('normal', 'late')]
        stats = {
            'worked':  len(worked_days),
            'absent':  sum(1 for d in month_data if d['status'] == 'absent'),
            'late':    sum(1 for d in month_data if d['status'] == 'late'),
            'total_hours': round(sum(d['hours'] for d in month_data), 1),
        }

        # 月曆格子（週一為第一欄）
        first_wd = month_data[0]['weekday']  # 0=Mon
        week = [None] * first_wd
        for day in month_data:
            week.append(day)
            if len(week) == 7:
                calendar_weeks.append(week)
                week = []
        if week:
            calendar_weeks.append(week + [None] * (7 - len(week)))

    # 上下月導覽
    prev_dt = date(year, month, 1) - timedelta(days=1)
    next_dt = date(year, month, days_in_month if month_data else 28) + timedelta(days=1)

    return render(request, 'reports/report.html', {
        'employees':       employees,
        'selected':        selected,
        'year':            year,
        'month':           month,
        'month_data':      month_data,
        'calendar_weeks':  calendar_weeks,
        'stats':           stats,
        'prev_year':       prev_dt.year,
        'prev_month':      prev_dt.month,
        'next_year':       next_dt.year,
        'next_month':      next_dt.month,
        'today':           today,
        'weekday_names':   ['一', '二', '三', '四', '五', '六', '日'],
    })
